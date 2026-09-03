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


def test_discard_all_removes_held_clips_and_their_files(tmp_path):
    staging = tmp_path / "clips_out"
    first = touch(staging / "a.mp4")
    poster = touch(staging / "a.jpg")
    second = touch(staging / "b.mp4")

    uploader = make_uploader(tmp_path)
    uploader.journal_path.write_text(
        json.dumps([
            {"mp4_path": str(first), "thumb_path": str(poster), "awaiting_user": True},
            {"mp4_path": str(second), "thumb_path": None, "awaiting_user": True},
        ]),
        encoding="utf-8",
    )

    assert uploader.discard_all() == 2
    assert not first.exists() and not poster.exists() and not second.exists()
    assert uploader.waiting() == []


def test_discard_all_spares_uploads_that_merely_failed(tmp_path):
    """A failed upload is the journal doing its job. Clearing held clips must
    not throw away a clip that is still trying to reach the server."""
    staging = tmp_path / "clips_out"
    held = touch(staging / "held.mp4")
    failed = touch(staging / "failed.mp4")

    uploader = make_uploader(tmp_path)
    uploader.journal_path.write_text(
        json.dumps([
            {"mp4_path": str(held), "thumb_path": None, "awaiting_user": True},
            {"mp4_path": str(failed), "thumb_path": None, "awaiting_user": False},
        ]),
        encoding="utf-8",
    )

    assert uploader.discard_all() == 1
    assert not held.exists()
    assert failed.exists(), "discarded a clip that was still pending upload"
    assert [e["mp4_path"] for e in uploader._load()] == [str(failed)]


def test_renaming_a_held_clip_sticks(tmp_path):
    staging = tmp_path / "clips_out"
    clip = touch(staging / "c.mp4")
    uploader = make_uploader(tmp_path)
    uploader.journal_path.write_text(
        json.dumps([{"mp4_path": str(clip), "thumb_path": None,
                     "awaiting_user": True, "title": "League of Legends"}]),
        encoding="utf-8",
    )

    assert uploader.rename(str(clip), "Baron steal") is True
    assert uploader.waiting()[0]["title"] == "Baron steal"


def test_renaming_to_nothing_clears_the_title(tmp_path):
    staging = tmp_path / "clips_out"
    clip = touch(staging / "d.mp4")
    uploader = make_uploader(tmp_path)
    uploader.journal_path.write_text(
        json.dumps([{"mp4_path": str(clip), "thumb_path": None,
                     "awaiting_user": True, "title": "something"}]),
        encoding="utf-8",
    )

    uploader.rename(str(clip), "   ")
    assert uploader.waiting()[0]["title"] is None


def test_renaming_an_unknown_clip_reports_failure(tmp_path):
    assert make_uploader(tmp_path).rename("nope.mp4", "x") is False
