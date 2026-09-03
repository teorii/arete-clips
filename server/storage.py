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

    def download(self, key: str, destination: Path) -> bool:
        target = self.path_for(key)
        if not target.exists():
            return False
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(target, destination)
        return True

    def upload(self, key: str, source: Path, content_type: str) -> int:
        return self.write(key, Path(source).read_bytes())


class S3Storage(StorageBackend):
    """Anything speaking the S3 API.

    One backend covers MinIO on this machine, MinIO on a server, Cloudflare R2
    and S3 itself, because presigned PUT and GET are the only operations used.
    That is what makes self-hosting and a managed bucket the same code path
    rather than two.
    """

    def __init__(self, settings: Settings) -> None:
        import boto3
        from botocore.config import Config

        if not settings.s3_endpoint_url:
            raise RuntimeError(
                "STORAGE_BACKEND=s3 needs S3_ENDPOINT_URL, for example "
                "http://127.0.0.1:9000 for a local MinIO"
            )

        self.bucket = settings.s3_bucket
        self.ttl = settings.upload_url_ttl_seconds
        # Falls back to the endpoint, which is what a local MinIO serves from.
        self.public_base = (
            settings.s3_public_base_url or f"{settings.s3_endpoint_url}/{settings.s3_bucket}"
        ).rstrip("/")
        config = Config(
            signature_version="s3v4",
            s3={"addressing_style": "path" if settings.s3_force_path_style else "auto"},
        )
        credentials = dict(
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            region_name=settings.s3_region,
            config=config,
        )

        # How this process reaches storage.
        self.client = boto3.client(
            "s3", endpoint_url=settings.s3_endpoint_url, **credentials
        )

        # What clients are told to upload to. A signature covers the host, so a
        # URL a remote machine can use has to be signed for the host it will
        # actually contact. Same client when there is no separate public
        # address, which is the single-machine case.
        public_endpoint = settings.s3_public_endpoint_url or settings.s3_endpoint_url
        self.signer = (
            self.client
            if public_endpoint == settings.s3_endpoint_url
            else boto3.client("s3", endpoint_url=public_endpoint, **credentials)
        )

    def ensure_bucket(self, public_read: bool = True) -> None:
        """Create the bucket, and let anyone read an object they can name.

        Playback has to work for someone who was handed a link and has no
        credentials, which is the entire point of a share link. The policy
        grants GetObject and nothing else: listing stays denied, so keys cannot
        be enumerated, and a key contains a UUIDv7 clip id that is not
        guessable. That is the same model as an R2 public bucket.

        Called explicitly by tools/setup_storage.py rather than at runtime,
        because making a bucket world-readable should be something you ran on
        purpose.
        """
        import json

        from botocore.exceptions import ClientError

        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError:
            self.client.create_bucket(Bucket=self.bucket)

        if not public_read:
            return
        self.client.put_bucket_policy(
            Bucket=self.bucket,
            Policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"AWS": ["*"]},
                            "Action": ["s3:GetObject"],
                            "Resource": [f"arn:aws:s3:::{self.bucket}/*"],
                        }
                    ],
                }
            ),
        )

    def create_upload(self, key: str, content_type: str) -> UploadTarget:
        url = self.signer.generate_presigned_url(
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

    def download(self, key: str, destination: Path) -> bool:
        from botocore.exceptions import ClientError

        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.client.download_file(self.bucket, key, str(destination))
        except ClientError:
            return False
        return True

    def upload(self, key: str, source: Path, content_type: str) -> int:
        source = Path(source)
        self.client.upload_file(
            str(source),
            self.bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )
        return source.stat().st_size


_backend: StorageBackend | None = None


def get_storage() -> StorageBackend:
    global _backend
    if _backend is None:
        settings = get_settings()
        # "r2" still accepted: it is the same protocol under a different name.
        if settings.storage_backend in {"s3", "r2"}:
            _backend = S3Storage(settings)
        else:
            _backend = LocalStorage(settings)
    return _backend
