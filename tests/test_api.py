"""End-to-end tests for the clip API.

These exercise the guarantees the capture client depends on: that a clip
cannot be marked ready without bytes behind it, that the client clock is not
trusted, and that timestamps leave the API unambiguous.
"""

from datetime import datetime, timedelta, timezone


def put_bytes(client, target: dict, payload: bytes):
    return client.request(
        target["method"], target["url"], content=payload, headers=target["headers"]
    )


def create_and_upload(client, payload: dict, video: bytes = b"fake-mp4-bytes"):
    """Walk the full happy path and return the ready clip."""
    created = client.post("/api/clips", json=payload).json()
    put_bytes(client, created["uploads"]["source"], video)
    labels = ["source"]
    if "thumb" in created["uploads"]:
        put_bytes(client, created["uploads"]["thumb"], b"jpeg")
        labels.append("thumb")
    done = client.post(
        f"/api/clips/{created['clipId']}/complete", json={"labels": labels}
    )
    assert done.status_code == 200, done.text
    return created, done.json()


# ---------------------------------------------------------------- write path


def test_create_returns_slug_and_upload_targets(client, sample_clip):
    response = client.post("/api/clips", json=sample_clip)
    assert response.status_code == 200, response.text
    body = response.json()

    assert len(body["publicSlug"]) == 10
    assert body["shareUrl"].endswith(f"/c/{body['publicSlug']}")
    assert set(body["uploads"]) == {"source", "thumb"}
    assert body["uploads"]["source"]["headers"]["Content-Type"] == "video/mp4"
    # The storage key must not be guessable from the public slug.
    assert body["publicSlug"] not in body["uploads"]["source"]["storageKey"]


def test_thumbnail_target_only_offered_when_client_has_one(client, sample_clip):
    body = client.post("/api/clips", json={**sample_clip, "hasThumbnail": False}).json()
    assert set(body["uploads"]) == {"source"}


def test_complete_rejects_a_clip_whose_bytes_never_arrived(client, sample_clip):
    """The guard that stops a broken client from creating unplayable clips."""
    created = client.post("/api/clips", json=sample_clip).json()

    response = client.post(
        f"/api/clips/{created['clipId']}/complete", json={"labels": ["source"]}
    )
    assert response.status_code == 409

    page = client.get("/api/clips").json()
    assert page["items"][0]["status"] == "failed"


def test_full_upload_marks_clip_ready_with_real_byte_counts(client, sample_clip):
    video = b"x" * 2048
    _, clip = create_and_upload(client, sample_clip, video)

    assert clip["status"] == "ready"
    assert clip["uploadedAt"] is not None
    source = next(r for r in clip["renditions"] if r["label"] == "source")
    # Size comes from storage, not from the client's claimed sourceBytes.
    assert source["bytes"] == len(video)
    assert source["status"] == "ready"


def test_upload_url_signature_is_enforced(client, sample_clip):
    created = client.post("/api/clips", json=sample_clip).json()
    tampered = created["uploads"]["source"]["url"].replace("sig=", "sig=00")
    assert client.put(tampered, content=b"nope").status_code == 403


# ------------------------------------------------------------- clock hygiene


def test_future_capture_time_is_clamped_to_server_clock(client, sample_clip):
    """The capture client's clock is untrusted and can be wrong or hostile."""
    ahead = datetime.now(timezone.utc) + timedelta(days=3650)
    body = client.post(
        "/api/clips", json={**sample_clip, "capturedAt": ahead.isoformat()}
    ).json()

    clip = client.get("/api/clips").json()["items"][0]
    assert clip["clipId"] == body["clipId"]
    assert datetime.fromisoformat(clip["capturedAt"]) <= datetime.now(
        timezone.utc
    ) + timedelta(minutes=1)


def test_timestamps_are_returned_timezone_aware(client, sample_clip):
    """Regression: SQLite has no timezone type, so values round-trip naive and
    serialize with no offset. A client then reads them as local time and every
    capture time silently shifts by the viewer's UTC offset."""
    _, clip = create_and_upload(client, sample_clip)

    for field in ("capturedAt", "uploadedAt"):
        parsed = datetime.fromisoformat(clip[field])
        assert parsed.tzinfo is not None, f"{field} came back without a timezone"


# ----------------------------------------------------------------- read path


def test_library_lists_newest_first(client, sample_clip):
    for hour in (20, 22, 21):
        create_and_upload(
            client,
            {**sample_clip, "capturedAt": f"2026-09-02T{hour}:00:00+00:00",
             "title": f"clip-{hour}"},
        )

    titles = [c["title"] for c in client.get("/api/clips").json()["items"]]
    assert titles == ["clip-22", "clip-21", "clip-20"]


def test_keyset_pagination_walks_every_clip_once(client, sample_clip):
    for minute in range(5):
        create_and_upload(
            client,
            {**sample_clip, "capturedAt": f"2026-09-02T22:0{minute}:00+00:00",
             "title": f"clip-{minute}"},
        )

    seen: list[str] = []
    cursor = None
    for _ in range(10):  # bounded so a cursor bug cannot hang the suite
        query = f"/api/clips?limit=2{f'&cursor={cursor}' if cursor else ''}"
        page = client.get(query).json()
        seen.extend(c["title"] for c in page["items"])
        cursor = page["nextCursor"]
        if not cursor:
            break

    assert cursor is None
    assert len(seen) == len(set(seen)) == 5


def test_malformed_cursor_is_rejected(client):
    assert client.get("/api/clips?cursor=not-base64").status_code == 400


def test_search_matches_title_and_slug(client, sample_clip):
    _, baron = create_and_upload(client, {**sample_clip, "title": "Baron steal"})
    create_and_upload(client, {**sample_clip, "title": "Dragon fight"})

    found = client.get("/api/clips?q=baron").json()["items"]
    assert [c["title"] for c in found] == ["Baron steal"]

    by_slug = client.get(f"/api/clips?q={baron['publicSlug']}").json()["items"]
    assert [c["clipId"] for c in by_slug] == [baron["clipId"]]

    assert client.get("/api/clips?q=nothing-matches").json()["items"] == []


# -------------------------------------------------------------------- update


def test_patch_renames_and_pins(client, sample_clip):
    _, clip = create_and_upload(client, sample_clip)
    assert clip["favorite"] is False

    updated = client.patch(
        f"/api/clips/{clip['clipId']}", json={"title": "Pentakill", "favorite": True}
    ).json()
    assert updated["title"] == "Pentakill"
    assert updated["favorite"] is True


def test_patch_only_touches_fields_that_were_sent(client, sample_clip):
    """A rename must not reset visibility or unpin the clip."""
    _, clip = create_and_upload(client, {**sample_clip, "title": "Original"})
    client.patch(f"/api/clips/{clip['clipId']}", json={"favorite": True})

    renamed = client.patch(
        f"/api/clips/{clip['clipId']}", json={"title": "Renamed"}
    ).json()
    assert renamed["title"] == "Renamed"
    assert renamed["favorite"] is True


def test_favorite_filter_splits_the_library(client, sample_clip):
    _, pinned = create_and_upload(client, {**sample_clip, "title": "Keeper"})
    create_and_upload(client, {**sample_clip, "title": "Filler"})
    client.patch(f"/api/clips/{pinned['clipId']}", json={"favorite": True})

    assert [c["title"] for c in client.get("/api/clips?favorite=true").json()["items"]] == ["Keeper"]
    assert [c["title"] for c in client.get("/api/clips?favorite=false").json()["items"]] == ["Filler"]


def test_patch_unknown_clip_is_404(client):
    missing = "00000000-0000-7000-8000-00000000dead"
    assert client.patch(f"/api/clips/{missing}", json={"title": "x"}).status_code == 404


# -------------------------------------------------------------------- delete


def test_delete_hides_the_clip_and_its_share_page(client, sample_clip):
    created, clip = create_and_upload(client, sample_clip)
    assert client.get(f"/c/{clip['publicSlug']}").status_code == 200

    assert client.delete(f"/api/clips/{clip['clipId']}").status_code == 204
    assert client.get("/api/clips").json()["items"] == []
    assert client.get(f"/c/{clip['publicSlug']}").status_code == 404
    assert client.delete(f"/api/clips/{clip['clipId']}").status_code == 404


# --------------------------------------------------------------- share page


def test_share_page_carries_embed_tags(client, sample_clip):
    _, clip = create_and_upload(client, sample_clip)
    html = client.get(f"/c/{clip['publicSlug']}").text

    # These are what make a pasted link unfurl into an inline player.
    assert 'property="og:type" content="video.other"' in html
    assert 'property="og:video:type" content="video/mp4"' in html
    assert 'name="twitter:card" content="player"' in html
    assert 'content="2560"' in html  # width from capture metadata


def test_viewing_a_clip_counts_a_view(client, sample_clip):
    _, clip = create_and_upload(client, sample_clip)
    client.get(f"/c/{clip['publicSlug']}")
    client.get(f"/c/{clip['publicSlug']}")

    assert client.get("/api/clips").json()["items"][0]["viewCount"] == 2
