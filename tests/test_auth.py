"""API key authentication.

The API is meant to be reachable from other machines, so the failure being
guarded against is concrete: someone with a share link listing the whole
library and deleting it.
"""

import pytest

WRITE_ROUTES = [
    ("post", "/api/clips", {"durationMs": 1000, "capturedAt": "2026-09-02T22:00:00+00:00"}),
    ("get", "/api/clips", None),
    ("patch", "/api/clips/00000000-0000-7000-8000-00000000dead", {"title": "x"}),
    ("delete", "/api/clips/00000000-0000-7000-8000-00000000dead", None),
    ("post", "/api/clips/00000000-0000-7000-8000-00000000dead/trim",
     {"startMs": 0, "endMs": 1000}),
]


@pytest.mark.parametrize("method,path,body", WRITE_ROUTES)
def test_routes_reject_anonymous_callers(anon_client, method, path, body):
    response = getattr(anon_client, method)(path, **({"json": body} if body else {}))
    assert response.status_code == 401, f"{method.upper()} {path} was reachable"


def test_a_wrong_key_is_rejected(anon_client):
    response = anon_client.get("/api/clips", headers={"X-API-Key": "arete_not_a_key"})
    assert response.status_code == 401


def test_share_pages_stay_public(client, anon_client, sample_clip):
    """A share link that needs a key is not a share link."""
    created = client.post("/api/clips", json=sample_clip).json()
    target = created["uploads"]["source"]
    client.request(target["method"], target["url"], content=b"v", headers=target["headers"])
    client.post(f"/api/clips/{created['clipId']}/complete", json={"labels": ["source"]})

    assert anon_client.get(f"/c/{created['publicSlug']}").status_code == 200
    assert anon_client.get("/healthz").status_code == 200


def test_rendition_files_stay_public(client, anon_client, sample_clip):
    created = client.post("/api/clips", json=sample_clip).json()
    target = created["uploads"]["source"]
    client.request(target["method"], target["url"], content=b"video", headers=target["headers"])
    done = client.post(
        f"/api/clips/{created['clipId']}/complete", json={"labels": ["source"]}
    ).json()

    url = next(r["url"] for r in done["renditions"] if r["label"] == "source")
    assert anon_client.get(url).status_code == 200, "playback needs to work for viewers"


def test_libraries_are_separate(client, other_client, sample_clip):
    client.post("/api/clips", json={**sample_clip, "title": "mine"})
    other_client.post("/api/clips", json={**sample_clip, "title": "theirs"})

    assert [c["title"] for c in client.get("/api/clips").json()["items"]] == ["mine"]
    assert [c["title"] for c in other_client.get("/api/clips").json()["items"]] == ["theirs"]


def test_you_cannot_delete_someone_elses_clip(client, other_client, sample_clip):
    mine = client.post("/api/clips", json={**sample_clip, "title": "mine"}).json()

    # 404 rather than 403: a 403 would confirm the clip exists.
    assert other_client.delete(f"/api/clips/{mine['clipId']}").status_code == 404
    assert client.get("/api/clips").json()["items"][0]["title"] == "mine"


def test_you_cannot_rename_or_trim_someone_elses_clip(client, other_client, sample_clip):
    mine = client.post("/api/clips", json={**sample_clip, "title": "mine"}).json()

    assert other_client.patch(
        f"/api/clips/{mine['clipId']}", json={"title": "hijacked"}
    ).status_code == 404
    assert other_client.post(
        f"/api/clips/{mine['clipId']}/trim", json={"startMs": 0, "endMs": 500}
    ).status_code == 404
