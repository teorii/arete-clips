"""Prepare the object store.

    python -m tools.setup_storage

Creates the bucket if it is missing and allows anonymous GetObject on its
contents, which is what lets someone open a share link without credentials.
Listing is not granted, so keys cannot be enumerated, and every key contains an
unguessable clip id.

Safe to run repeatedly.
"""

from __future__ import annotations

import sys

from server.config import get_settings
from server.storage import S3Storage


def main() -> int:
    settings = get_settings()
    if settings.storage_backend not in {"s3", "r2"}:
        print(
            f"STORAGE_BACKEND is {settings.storage_backend!r}; nothing to set up. "
            "Set it to 's3' once the object store is running."
        )
        return 0

    storage = S3Storage(settings)
    print(f"endpoint : {settings.s3_endpoint_url}")
    print(f"bucket   : {settings.s3_bucket}")
    storage.ensure_bucket(public_read=True)
    print("bucket ready, objects are publicly readable by URL")
    print(f"playback : {storage.playback_url('<key>')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
