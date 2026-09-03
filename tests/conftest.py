"""Test environment.

Settings are read once at import and the SQLAlchemy engine is bound at import
time, so every override has to be in place before anything under server/ is
imported. That is why this file sets environment variables at module top,
before the imports below.
"""

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="clipper_tests_"))

# TestClient talks to http://testserver, so pointing the public base URL at the
# same host means the presigned upload URLs it hands back are directly usable.
os.environ["PUBLIC_BASE_URL"] = "http://testserver"
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'test.db').as_posix()}"
os.environ["STORAGE_BACKEND"] = "local"
os.environ["LOCAL_STORAGE_DIR"] = str(_TMP / "storage")
os.environ["UPLOAD_SECRET"] = "test-secret"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from server.db import engine  # noqa: E402
from server.main import app  # noqa: E402
from server.models import Base  # noqa: E402


@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as test_client:
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
