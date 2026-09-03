"""Issue, rotate and revoke API keys.

    python -m tools.add_user --handle seth --adopt-existing
    python -m tools.add_user --handle seth --reissue
    python -m tools.add_user --revoke james
    python -m tools.add_user --list

A key is shown once and only its SHA-256 is stored, so it cannot be recovered,
only rotated. Nothing that can fail is allowed to run between committing a key
and printing it.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select

from server.auth import generate_key, hash_key
from server.db import SessionLocal
from server.ids import uuid7
from server.migrate import ensure_schema
from server.models import Clip, User

# Clips captured before keys existed carry this owner. Adopting it hands an
# existing library to a real user rather than orphaning it.
LEGACY_OWNER_ID = uuid.UUID("00000000-0000-7000-8000-000000000001")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="add_user")
    parser.add_argument("--handle", help="a name for this person")
    parser.add_argument(
        "--adopt-existing",
        action="store_true",
        help="take ownership of clips captured before keys existed",
    )
    parser.add_argument(
        "--reissue",
        action="store_true",
        help="rotate the key of an existing handle, keeping their clips",
    )
    parser.add_argument("--revoke", metavar="HANDLE", help="disable a key")
    parser.add_argument("--list", action="store_true", help="show existing handles")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ensure_schema()
    session = SessionLocal()
    try:
        if args.list:
            users = session.scalars(select(User).order_by(User.handle)).all()
            if not users:
                print("No users yet.")
            for user in users:
                owned = session.scalar(
                    select(func.count())
                    .select_from(Clip)
                    .where(Clip.owner_id == user.id, Clip.deleted_at.is_(None))
                )
                state = "revoked" if user.disabled_at else "active"
                print(f"  {user.handle:20s} {state:8s} {owned} clip(s)")
            return 0

        if args.revoke:
            user = session.scalar(select(User).where(User.handle == args.revoke))
            if user is None:
                print(f"No user called {args.revoke!r}.")
                return 1
            user.disabled_at = datetime.now(timezone.utc)
            session.commit()
            print(f"Revoked {args.revoke!r}. Their clips are untouched.")
            print(f"To give them access again: --handle {args.revoke} --reissue")
            return 0

        if not args.handle:
            print("Need --handle, --revoke or --list.")
            return 1

        existing = session.scalar(select(User).where(User.handle == args.handle))
        if existing is not None and not args.reissue:
            print(
                f"{args.handle!r} already exists. To rotate their key, keeping "
                f"their clips:\n  --handle {args.handle} --reissue"
            )
            return 1
        if existing is None and args.reissue:
            print(f"No user called {args.handle!r} to reissue for.")
            return 1

        owner_id = (
            existing.id
            if existing is not None
            else (LEGACY_OWNER_ID if args.adopt_existing else uuid7())
        )
        # Counted before anything is written. Nothing that can raise may sit
        # between the commit and printing the key, or a failure loses a key that
        # is only stored as a hash.
        owned = session.scalar(
            select(func.count())
            .select_from(Clip)
            .where(Clip.owner_id == owner_id, Clip.deleted_at.is_(None))
        )

        key = generate_key()
        if existing is not None:
            existing.api_key_hash = hash_key(key)
            existing.disabled_at = None
            action = "Rotated the key for"
        else:
            session.add(
                User(id=owner_id, handle=args.handle, api_key_hash=hash_key(key))
            )
            action = "Created"
        session.commit()

        print(f"\n{action}: {args.handle}")
        print(f"Key: {key}")
        print(f"Owns {owned} clip(s).")
        print("\nPut this in that machine's .env as:")
        print(f"  ARETE_API_KEY={key}")
        print("\nShown once. Only the hash is stored, so it can be rotated, not read.")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
