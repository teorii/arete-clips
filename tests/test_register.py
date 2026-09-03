"""Self-registration.

Lets a new machine join with one shared invite code instead of a key issued by
hand on the host. The gate matters because the server sits on a public URL:
libraries are separate, so a stranger never sees anyone else's clips, but they
would share the host's disk.
"""

import pytest
from sqlalchemy import select

from server.auth import hash_key
from server.db import SessionLocal
from server.models import User


@pytest.fixture
def open_server(monkeypatch):
    """A host that is accepting new accounts."""
    from server import main

    monkeypatch.setattr(main.settings, "invite_code", "let-me-in", raising=False)
    return "let-me-in"


def test_registration_is_closed_when_no_invite_is_set(anon_client):
    """Default is closed: a server nobody should join must not accept one."""
    response = anon_client.post(
        "/api/register", json={"handle": "stranger", "invite": ""}
    )
    assert response.status_code == 403


def test_a_wrong_invite_is_refused(anon_client, open_server):
    response = anon_client.post(
        "/api/register", json={"handle": "stranger", "invite": "guess"}
    )
    assert response.status_code == 403


def test_the_right_invite_creates_an_account_and_returns_a_working_key(
    anon_client, open_server
):
    response = anon_client.post(
        "/api/register", json={"handle": "james", "invite": open_server}
    )
    assert response.status_code == 200, response.text
    key = response.json()["apiKey"]

    session = SessionLocal()
    try:
        user = session.scalar(select(User).where(User.handle == "james"))
    finally:
        session.close()
    assert user is not None
    assert user.api_key_hash == hash_key(key), "returned key does not match the hash"

    # The key must actually work, not merely exist.
    assert anon_client.get("/api/clips", headers={"X-API-Key": key}).status_code == 200


def test_a_taken_name_is_refused(anon_client, open_server):
    anon_client.post("/api/register", json={"handle": "james", "invite": open_server})
    again = anon_client.post(
        "/api/register", json={"handle": "james", "invite": open_server}
    )
    assert again.status_code == 409


def test_a_new_account_sees_an_empty_library(client, anon_client, open_server, sample_clip):
    """Joining must not expose the host's clips."""
    client.post("/api/clips", json={**sample_clip, "title": "host clip"})

    key = anon_client.post(
        "/api/register", json={"handle": "newcomer", "invite": open_server}
    ).json()["apiKey"]

    theirs = anon_client.get("/api/clips", headers={"X-API-Key": key}).json()
    assert theirs["items"] == [], "a new account could see someone else's clips"


def test_a_blank_name_is_refused(anon_client, open_server):
    response = anon_client.post(
        "/api/register", json={"handle": "   ", "invite": open_server}
    )
    assert response.status_code == 422
