"""Time-ordered ids and public slugs.

UUIDv7 puts a millisecond timestamp in the high bits, so primary keys sort by
creation time. That gives index locality on insert and rough creation order for
free. The public slug is separate and random, so nothing enumerable ever shows
up in a share URL.
"""

import os
import secrets
import time
import uuid

_BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def uuid7() -> uuid.UUID:
    """RFC 9562 UUIDv7: 48-bit unix ms timestamp, then random."""
    ms = int(time.time() * 1000)
    raw = bytearray(ms.to_bytes(6, "big") + os.urandom(10))
    raw[6] = (raw[6] & 0x0F) | 0x70  # version 7
    raw[8] = (raw[8] & 0x3F) | 0x80  # RFC 4122 variant
    return uuid.UUID(bytes=bytes(raw))


def public_slug(length: int = 10) -> str:
    """Unguessable base62 slug. 62^10 keeps brute-force scanning pointless."""
    return "".join(secrets.choice(_BASE62) for _ in range(length))
