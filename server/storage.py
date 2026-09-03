"""Object storage behind one interface.

The client always does the same three steps: ask the API where to put bytes,
PUT the bytes at that URL, then tell the API it finished. Whether that URL
points at Cloudflare R2 or at a local dev endpoint is invisible to the client,
so moving to real object storage is an env var rather than a rewrite.

The rule this preserves: video bytes never pass through the application tier in
production. In local mode they do, which is the one honest compromise here and
is confined to LocalStorage.
"""

from __future__ import annotations

import hashlib
import hmac
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
            url=f"{self.base_url}/dev-upload?{qs}",
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


class R2Storage(StorageBackend):
    """Cloudflare R2. S3-compatible, and zero egress fees, which is the whole
    reason to pick it for video."""

    def __init__(self, settings: Settings) -> None:
        import boto3
        from botocore.config import Config

        self.bucket = settings.r2_bucket
        self.ttl = settings.upload_url_ttl_seconds
        self.public_base = settings.r2_public_base_url.rstrip("/")
        self.client = boto3.client(
            "s3",
            endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            region_name="auto",
            config=Config(signature_version="s3v4"),
        )

    def create_upload(self, key: str, content_type: str) -> UploadTarget:
        url = self.client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=self.ttl,
        )
        # The signature covers Content-Type, so the client must send it back.
        return UploadTarget(url=url, headers={"Content-Type": content_type})

    def playback_url(self, key: str) -> str:
        return f"{self.public_base}/{quote(key)}"

    def size_of(self, key: str) -> int | None:
        from botocore.exceptions import ClientError

        try:
            return self.client.head_object(Bucket=self.bucket, Key=key)["ContentLength"]
        except ClientError:
            return None

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


_backend: StorageBackend | None = None


def get_storage() -> StorageBackend:
    global _backend
    if _backend is None:
        settings = get_settings()
        _backend = (
            R2Storage(settings)
            if settings.storage_backend == "r2"
            else LocalStorage(settings)
        )
    return _backend
