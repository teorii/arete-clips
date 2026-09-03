import time

import pytest

from server.config import Settings
from server.storage import LocalStorage


def make_storage(tmp_path, ttl: int = 3600) -> LocalStorage:
    return LocalStorage(
        Settings(
            local_storage_dir=tmp_path,
            public_base_url="http://testserver",
            upload_secret="test-secret",
            upload_url_ttl_seconds=ttl,
        )
    )


def test_signed_upload_url_verifies(tmp_path):
    storage = make_storage(tmp_path)
    target = storage.create_upload("clips/a/source.mp4", "video/mp4")

    assert target.method == "PUT"
    assert target.headers["Content-Type"] == "video/mp4"

    from urllib.parse import parse_qs, urlparse

    params = parse_qs(urlparse(target.url).query)
    assert storage.verify(
        params["key"][0], int(params["expires"][0]), params["sig"][0]
    )


def test_signature_does_not_transfer_to_another_key(tmp_path):
    """Without this, anyone holding one upload URL could overwrite any object."""
    storage = make_storage(tmp_path)
    expires = int(time.time()) + 600
    signature = storage._sign("clips/mine.mp4", expires)

    assert storage.verify("clips/mine.mp4", expires, signature)
    assert not storage.verify("clips/someone-else.mp4", expires, signature)


def test_expired_signature_is_rejected(tmp_path):
    storage = make_storage(tmp_path)
    expired = int(time.time()) - 1
    assert not storage.verify("clips/a.mp4", expired, storage._sign("clips/a.mp4", expired))


def test_key_cannot_escape_the_storage_root(tmp_path):
    storage = make_storage(tmp_path)
    with pytest.raises(ValueError):
        storage.path_for("../../../etc/passwd")


def test_write_size_and_delete_roundtrip(tmp_path):
    storage = make_storage(tmp_path)
    key = "clips/2026/09/abc/source.mp4"

    assert storage.size_of(key) is None
    assert storage.write(key, b"video-bytes") == 11
    assert storage.size_of(key) == 11

    storage.delete(key)
    assert storage.size_of(key) is None
