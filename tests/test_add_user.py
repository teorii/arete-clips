"""The key management tool.

Regression: it committed the user, then ran a query that could fail, and only
printed the key afterwards. When that query raised, the user existed with a key
nobody would ever see, and the existence check refused to reissue. Two bugs that
between them made a library unreachable.
"""

import pytest
from sqlalchemy import select

from server.auth import hash_key
from server.db import SessionLocal
from server.models import Clip, User
from tools.add_user import LEGACY_OWNER_ID, main


def key_from(capsys) -> str:
    for line in capsys.readouterr().out.splitlines():
        if line.startswith("Key: "):
            return line.split("Key: ", 1)[1].strip()
    raise AssertionError("the tool did not print a key")


def user_named(handle: str) -> User | None:
    session = SessionLocal()
    try:
        return session.scalar(select(User).where(User.handle == handle))
    finally:
        session.close()


def test_creates_a_user_and_prints_a_working_key(api_key, capsys):
    assert main(["--handle", "alice"]) == 0
    key = key_from(capsys)

    user = user_named("alice")
    assert user is not None
    assert user.api_key_hash == hash_key(key), "printed key does not match the stored hash"


def test_creating_a_duplicate_handle_is_refused(api_key, capsys):
    main(["--handle", "alice"])
    capsys.readouterr()

    assert main(["--handle", "alice"]) == 1
    assert "--reissue" in capsys.readouterr().out, "message must say how to recover"


def test_reissue_rotates_the_key_without_moving_the_clips(api_key, client, sample_clip, capsys):
    """The exact recovery path from the bug: a user exists but their key is
    unrecoverable, and their clips must stay theirs."""
    main(["--handle", "bob"])
    first = key_from(capsys)
    bob = user_named("bob")

    session = SessionLocal()
    try:
        session.add(
            Clip(
                public_slug="bobclip01",
                owner_id=bob.id,
                status="ready",
                visibility="unlisted",
                trigger_type="hotkey",
                duration_ms=1000,
                captured_at=__import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ),
            )
        )
        session.commit()
    finally:
        session.close()

    assert main(["--handle", "bob", "--reissue"]) == 0
    output = capsys.readouterr().out
    second = [line for line in output.splitlines() if line.startswith("Key: ")][0][5:].strip()

    assert second != first, "reissue returned the same key"
    assert "Owns 1 clip(s)" in output, "clip count lost on reissue"

    after = user_named("bob")
    assert after.id == bob.id, "reissue moved the user id, orphaning their clips"
    assert after.api_key_hash == hash_key(second)


def test_reissue_reactivates_a_revoked_user(api_key, capsys):
    main(["--handle", "carol"])
    capsys.readouterr()
    assert main(["--revoke", "carol"]) == 0
    assert user_named("carol").disabled_at is not None

    assert main(["--handle", "carol", "--reissue"]) == 0
    assert user_named("carol").disabled_at is None, "revoked user stayed disabled"


def test_reissue_without_an_existing_user_is_refused(api_key):
    assert main(["--handle", "nobody", "--reissue"]) == 1


def test_adopt_existing_claims_the_legacy_owner_id(api_key, capsys):
    assert main(["--handle", "dave", "--adopt-existing"]) == 0
    capsys.readouterr()
    assert user_named("dave").id == LEGACY_OWNER_ID


def test_revoking_an_unknown_handle_is_refused(api_key):
    assert main(["--revoke", "ghost"]) == 1
