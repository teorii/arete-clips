"""Test environment.

Settings are read once at import and the SQLAlchemy engine is bound at import
time, so every override has to be in place before anything under server/ is
imported. That is why this file sets environment variables at module top,
before the imports below.
"""

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="arete_tests_"))

# TestClient talks to http://testserver, so pointing the public base URL at the
# same host means the presigned upload URLs it hands back are directly usable.
os.environ["PUBLIC_BASE_URL"] = "http://testserver"
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'test.db').as_posix()}"
os.environ["STORAGE_BACKEND"] = "local"
os.environ["LOCAL_STORAGE_DIR"] = str(_TMP / "storage")
os.environ["UPLOAD_SECRET"] = "test-secret"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from server.auth import generate_key, hash_key  # noqa: E402
from server.db import SessionLocal, engine  # noqa: E402
from server.ids import uuid7  # noqa: E402
from server.main import app  # noqa: E402
from server.models import Base, User  # noqa: E402


def _make_user(handle: str) -> str:
    """Create a user and return their key."""
    key = generate_key()
    session = SessionLocal()
    try:
        session.add(User(id=uuid7(), handle=handle, api_key_hash=hash_key(key)))
        session.commit()
    finally:
        session.close()
    return key


@pytest.fixture
def api_key():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    return _make_user("tester")


@pytest.fixture
def client(api_key):
    """Authenticated as the default test user."""
    with TestClient(app, headers={"X-API-Key": api_key}) as test_client:
        yield test_client


@pytest.fixture
def anon_client(api_key):
    """No credentials. The schema is already set up by the api_key fixture."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def other_client(api_key):
    """A second, unrelated user."""
    key = _make_user("someone-else")
    with TestClient(app, headers={"X-API-Key": key}) as test_client:
        yield test_client


@pytest.fixture
def sample_clip():
    """Payload for POST /api/clips, matching what the capture daemon sends."""
    return {
        "durationMs": 30_000,
        "capturedAt": "2026-09-02T22:00:00+00:00",
        "sourceBytes": 44_100_000,
        "contentHash": "a" * 64,
        "captureMeta": {"encoder": "h264_nvenc", "width": 2560, "height": 1440, "fps": 60},
        "triggerType": "hotkey",
        "hasThumbnail": True,
    }
