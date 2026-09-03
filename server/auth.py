"""API key authentication.

Reading a clip is public: that is what a share link is. Everything else needs a
key, because the alternative on a reachable API is that anyone who finds a link
can list your whole library and delete it.

Keys are issued with tools/add_user.py and live in each client's .env. Only the
SHA-256 is stored, so the database never holds anything usable.
"""

from __future__ import annotations

import hashlib
import secrets

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import User

KEY_PREFIX = "arete_"


def generate_key() -> str:
    return f"{KEY_PREFIX}{secrets.token_hex(24)}"


def hash_key(key: str) -> str:
    return hashlib.sha256(key.strip().encode()).hexdigest()


def require_user(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> User:
    if not x_api_key:
        raise HTTPException(401, "missing X-API-Key header")

    user = db.scalar(select(User).where(User.api_key_hash == hash_key(x_api_key)))
    # Same message either way: distinguishing "no such key" from "disabled key"
    # tells an attacker which guesses were close.
    if user is None or user.disabled_at is not None:
        raise HTTPException(401, "invalid API key")
    return user
