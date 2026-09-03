"""Staging cleanup.

Every clip is written to the staging directory, then uploaded to object
storage. Without cleanup that leaves a second full-size copy of every clip on
disk forever, and deleting a clip in the library frees only half of it.
"""

import json

from capture.uploader import Uploader


def make_uploader(tmp_path):
    return Uploader("http://testserver", tmp_path / "journal.json")


def touch(path, size: int = 32):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def test_sweep_removes_files_the_journal_does_not_know_about(tmp_path):
    staging = tmp_path / "clips_out"
    uploaded = touch(staging / "clip_a.mp4")
    poster = touch(staging / "clip_a.jpg")

    assert make_uploader(tmp_path).sweep_staging(staging) == 2
    assert not uploaded.exists()
    assert not poster.exists()


def test_sweep_keeps_clips_still_waiting_to_upload(tmp_path):
    """A queued upload has not reached storage yet, so its staging file is the
    only copy in existence. Deleting it would lose the clip."""
    staging = tmp_path / "clips_out"
    pending = touch(staging / "pending.mp4")
    pending_thumb = touch(staging / "pending.jpg")
    stale = touch(staging / "already_uploaded.mp4")

    uploader = make_uploader(tmp_path)
    uploader.journal_path.write_text(
        json.dumps([{"mp4_path": str(pending), "thumb_path": str(pending_thumb)}]),
        encoding="utf-8",
    )

    assert uploader.sweep_staging(staging) == 1
    assert pending.exists(), "deleted a clip that had not been uploaded"
    assert pending_thumb.exists()
    assert not stale.exists()


def test_sweep_on_a_missing_directory_is_harmless(tmp_path):
    assert make_uploader(tmp_path).sweep_staging(tmp_path / "nope") == 0


def test_discard_local_tolerates_missing_files(tmp_path):
    present = touch(tmp_path / "there.mp4")
    Uploader._discard_local(present, tmp_path / "gone.jpg", None)
    assert not present.exists()
