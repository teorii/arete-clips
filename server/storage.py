"""Where clip files live.

The client asks the API for somewhere to put bytes, PUTs them there, then says
it finished. That indirection is what lets a second machine upload to a host it
does not share a disk with: the URL it is handed is signed, time limited, and
points at the host rather than at anything local to it.

Files are served from a folder. An install is meant to need nothing installed,
and an object store would be a second service to run for no benefit at this
size.
"""

from __future__ import annotations

import hashlib
import hmac
import shutil
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlencode

from .config import Settings, get_settings


@dataclass
class UploadTarget:
    url: str
    method: str = "PUT"
    headers: dict[str, str] = field(default_factory=dict)


class StorageBackend(ABC):
    @abstractmethod
    def create_upload(self, key: str, content_type: str) -> UploadTarget: ...

    @abstractmethod
    def playback_url(self, key: str) -> str: ...

    @abstractmethod
    def size_of(self, key: str) -> int | None:
        """Bytes if the object landed, None if it is missing.

        Used by /complete so a client cannot mark a clip ready without having
        actually uploaded anything.
        """

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def download(self, key: str, destination: Path) -> bool:
        """Fetch an object to a local file. False if it is not there.

        Editing needs the bytes back. Keeping this on the interface is what lets
        trimming work the same way whether storage is a folder or a bucket.
        """

    @abstractmethod
    def upload(self, key: str, source: Path, content_type: str) -> int:
        """Replace an object from a local file, returning its size."""


def sign_held(name: str, expires: int) -> str:
    """Sign a held clip's name so the library can play it.

    A <video> element cannot send an API key header, and this install may be
    reachable through a tunnel, so the file cannot simply be public. The same
    secret that signs an upload target signs a short-lived preview instead.
    """
    from .config import get_settings

    secret = get_settings().upload_secret.encode()
    return hmac.new(secret, f"held:{name}:{expires}".encode(), hashlib.sha256).hexdigest()


def verify_held(name: str, expires: int, signature: str) -> bool:
    if expires < int(time.time()):
        return False
    return hmac.compare_digest(sign_held(name, expires), signature)


class LocalStorage(StorageBackend):
    """Dev backend. Mirrors the presigned contract with an HMAC-signed URL."""

    def __init__(self, settings: Settings) -> None:
        self.root = Path(settings.local_storage_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.base_url = settings.public_base_url.rstrip("/")
        self.secret = settings.upload_secret.encode()
        self.ttl = settings.upload_url_ttl_seconds

    def _sign(self, key: str, expires: int) -> str:
        msg = f"{key}:{expires}".encode()
        return hmac.new(self.secret, msg, hashlib.sha256).hexdigest()

    def verify(self, key: str, expires: int, sig: str) -> bool:
        if expires < int(time.time()):
            return False
        return hmac.compare_digest(self._sign(key, expires), sig)

    def path_for(self, key: str) -> Path:
        # Resolve and confine: a key must never escape the storage root.
        target = (self.root / key).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError("key escapes storage root")
        return target

    def create_upload(self, key: str, content_type: str) -> UploadTarget:
        expires = int(time.time()) + self.ttl
        qs = urlencode({"key": key, "expires": expires, "sig": self._sign(key, expires)})
        return UploadTarget(
            url=f"{self.base_url}/upload?{qs}",
            headers={"Content-Type": content_type},
        )

    def write(self, key: str, data: bytes) -> int:
        target = self.path_for(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return len(data)

    def playback_url(self, key: str) -> str:
        return f"{self.base_url}/files/{quote(key)}"

    def size_of(self, key: str) -> int | None:
        target = self.path_for(key)
        return target.stat().st_size if target.exists() else None

    def delete(self, key: str) -> None:
        target = self.path_for(key)
        if target.exists():
            target.unlink()

    def download(self, key: str, destination: Path) -> bool:
        target = self.path_for(key)
        if not target.exists():
            return False
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(target, destination)
        return True

    def upload(self, key: str, source: Path, content_type: str) -> int:
        return self.write(key, Path(source).read_bytes())


_backend: StorageBackend | None = None


def reset_storage() -> None:
    """Forget the cached backend.

    The addresses a backend was built with can change while the process runs:
    opening a tunnel rewrites where clients are told to upload. Without this the
    first backend built keeps signing URLs for the old address.
    """
    global _backend
    _backend = None


def get_storage() -> StorageBackend:
    global _backend
    if _backend is None:
        settings = get_settings()
        _backend = LocalStorage(settings)
    return _backend
