"""Trimming.

Uses a real encoded video, because the whole operation is ffmpeg remuxing an
actual file: fake bytes would only prove the endpoint returns a status code.
The fixture builds a 6 second clip with keyframes every 2 seconds, matching
what the capture side produces.
"""

import subprocess

import pytest

from capture.ffmpeg import FFMPEG

pytestmark = pytest.mark.skipif(not FFMPEG, reason="ffmpeg not available")


@pytest.fixture(scope="module")
def video_bytes(tmp_path_factory) -> bytes:
    path = tmp_path_factory.mktemp("media") / "source.mp4"
    result = subprocess.run(
        [
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30", "-t", "6",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-g", "60", "-force_key_frames", "expr:gte(t,n_forced*2)",
            str(path),
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return path.read_bytes()


@pytest.fixture
def uploaded(client, sample_clip, video_bytes):
    payload = {**sample_clip, "durationMs": 6000, "hasThumbnail": True}
    created = client.post("/api/clips", json=payload).json()
    for label, blob in (("source", video_bytes), ("thumb", b"jpeg-placeholder")):
        target = created["uploads"][label]
        client.request(
            target["method"], target["url"], content=blob, headers=target["headers"]
        )
    client.post(
        f"/api/clips/{created['clipId']}/complete",
        json={"labels": ["source", "thumb"]},
    )
    return created


def test_trim_shortens_the_clip(client, uploaded):
    before = client.get("/api/clips").json()["items"][0]

    trimmed = client.post(
        f"/api/clips/{uploaded['clipId']}/trim", json={"startMs": 2000, "endMs": 4000}
    )
    assert trimmed.status_code == 200, trimmed.text
    body = trimmed.json()

    assert body["durationMs"] < before["durationMs"]
    assert 1500 <= body["durationMs"] <= 2600, body["durationMs"]

    source = next(r for r in body["renditions"] if r["label"] == "source")
    original = next(r for r in before["renditions"] if r["label"] == "source")
    assert source["bytes"] < original["bytes"], "trimmed file is not smaller"


def test_trimmed_clip_is_still_playable(client, uploaded):
    client.post(
        f"/api/clips/{uploaded['clipId']}/trim", json={"startMs": 0, "endMs": 2000}
    )
    clip = client.get("/api/clips").json()["items"][0]
    source = next(r for r in clip["renditions"] if r["label"] == "source")

    played = client.get(source["url"])
    assert played.status_code == 200
    # An MP4 the browser can start is the point; ftyp is the opening box.
    assert b"ftyp" in played.content[:64]


def test_poster_is_regenerated_from_the_trimmed_clip(client, uploaded):
    before = client.get("/api/clips").json()["items"][0]
    old_thumb = next(r for r in before["renditions"] if r["label"] == "thumb")

    body = client.post(
        f"/api/clips/{uploaded['clipId']}/trim", json={"startMs": 2000, "endMs": 4000}
    ).json()

    new_thumb = next(r for r in body["renditions"] if r["label"] == "thumb")
    assert new_thumb["bytes"] != old_thumb["bytes"], "poster still shows the old frame"


def test_end_before_start_is_rejected(client, uploaded):
    response = client.post(
        f"/api/clips/{uploaded['clipId']}/trim", json={"startMs": 4000, "endMs": 2000}
    )
    assert response.status_code == 422


def test_a_failed_trim_leaves_the_original_intact(client, uploaded):
    """The replace happens only after the new file is known good, so a bad cut
    must not destroy the clip it was editing."""
    before = client.get("/api/clips").json()["items"][0]

    response = client.post(
        f"/api/clips/{uploaded['clipId']}/trim",
        json={"startMs": 60_000, "endMs": 62_000},
    )
    assert response.status_code == 422

    after = client.get("/api/clips").json()["items"][0]
    source = next(r for r in after["renditions"] if r["label"] == "source")
    original = next(r for r in before["renditions"] if r["label"] == "source")
    assert source["bytes"] == original["bytes"], "original was damaged by a failed trim"
    assert client.get(source["url"]).status_code == 200


def test_trimming_an_unknown_clip_is_404(client):
    missing = "00000000-0000-7000-8000-00000000dead"
    response = client.post(
        f"/api/clips/{missing}/trim", json={"startMs": 0, "endMs": 1000}
    )
    assert response.status_code == 404


def test_trim_changes_the_playback_url(client, uploaded):
    """Regression: trimming rewrites the file in place, so the key stays the
    same. If the URL does not change with it, the browser, the share page and
    any embed all keep serving the old clip and the trim looks like it did
    nothing."""
    before = client.get("/api/clips").json()["items"][0]
    before_urls = {r["label"]: r["url"] for r in before["renditions"]}

    body = client.post(
        f"/api/clips/{uploaded['clipId']}/trim", json={"startMs": 2000, "endMs": 4000}
    ).json()
    after_urls = {r["label"]: r["url"] for r in body["renditions"]}

    assert after_urls["source"] != before_urls["source"], "source URL did not change"
    assert after_urls["thumb"] != before_urls["thumb"], "poster URL did not change"
    # Same object, new version: the key must not have moved.
    assert after_urls["source"].split("?")[0] == before_urls["source"].split("?")[0]


def test_share_page_serves_the_versioned_url(client, uploaded):
    client.post(
        f"/api/clips/{uploaded['clipId']}/trim", json={"startMs": 0, "endMs": 2000}
    )
    clip = client.get("/api/clips").json()["items"][0]
    html = client.get(f"/c/{clip['publicSlug']}").text

    source = next(r for r in clip["renditions"] if r["label"] == "source")
    assert source["url"] in html, "share page still points at the pre-trim URL"
