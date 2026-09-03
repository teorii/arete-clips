"""Move an existing library onto the self-hosted stack.

    python -m tools.migrate_data --source sqlite:///./recording.db --files ./clips_local

Copies users, clips and renditions from the old database into whatever
DATABASE_URL now points at, then uploads each clip's files into whatever
STORAGE_BACKEND now points at.

Safe to re-run: rows already present are skipped by primary key, and a file
already in storage at the right size is not re-uploaded. A half-finished
migration can simply be run again.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.orm import Session

from server.db import engine as target_engine
from server.migrate import ensure_schema
from server.models import Clip, ClipRendition, User
from server.storage import get_storage

# Parents before children, or the foreign keys fail.
ORDER = [User, Clip, ClipRendition]


def primary_key(model, row):
    return tuple(getattr(row, column.name) for column in model.__table__.primary_key)


def copy_rows(source_url: str) -> dict[str, tuple[int, int]]:
    source_engine = sa.create_engine(source_url)
    counts: dict[str, tuple[int, int]] = {}

    with Session(source_engine) as source, Session(target_engine) as target:
        for model in ORDER:
            rows = source.scalars(sa.select(model)).all()
            copied = skipped = 0
            for row in rows:
                if target.get(model, primary_key(model, row)) is not None:
                    skipped += 1
                    continue
                # Detach from the source session and re-attach to the target,
                # keeping ids so clips stay owned by the same user.
                values = {
                    column.name: getattr(row, column.name)
                    for column in model.__table__.columns
                }
                target.add(model(**values))
                copied += 1
            target.commit()
            counts[model.__tablename__] = (copied, skipped)
    return counts


def copy_files(files_dir: Path) -> tuple[int, int, int]:
    storage = get_storage()
    uploaded = present = missing = 0

    with Session(target_engine) as session:
        renditions = session.scalars(
            sa.select(ClipRendition).where(ClipRendition.storage_key.is_not(None))
        ).all()
        for rendition in renditions:
            local = files_dir / rendition.storage_key
            existing = storage.size_of(rendition.storage_key)
            if existing and (not local.exists() or existing == local.stat().st_size):
                present += 1
                continue
            if not local.exists():
                missing += 1
                print(f"  missing on disk: {rendition.storage_key}")
                continue
            content_type = "image/jpeg" if rendition.label == "thumb" else "video/mp4"
            size = storage.upload(rendition.storage_key, local, content_type)
            rendition.bytes = size
            uploaded += 1
            print(f"  uploaded {rendition.label:6s} {size / 1_048_576:6.1f} MB  {rendition.storage_key}")
        session.commit()
    return uploaded, present, missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="migrate_data")
    parser.add_argument("--source", help="old database URL, e.g. sqlite:///./recording.db")
    parser.add_argument("--files", help="directory holding the old clip files")
    args = parser.parse_args(argv)

    if not args.source and not args.files:
        print("Nothing to do. Pass --source, --files, or both.")
        return 1

    ensure_schema()

    if args.source:
        print(f"Rows from {args.source}")
        for table, (copied, skipped) in copy_rows(args.source).items():
            print(f"  {table:16s} {copied} copied, {skipped} already there")

    if args.files:
        directory = Path(args.files)
        if not directory.is_dir():
            print(f"No such directory: {directory}")
            return 1
        print(f"Files from {directory}")
        uploaded, present, missing = copy_files(directory)
        print(f"  {uploaded} uploaded, {present} already in storage, {missing} missing")
        if missing:
            print("  Missing files mean those clips will 404. Nothing else was changed.")
            return 1

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
