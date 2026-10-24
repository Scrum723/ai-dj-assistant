"""B3 booth-pass identity: OAuth callbacks, link codes, profile_id, memory merge."""

from __future__ import annotations

import json
import os
import time
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def identity_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from community.identity import IdentityStore, set_identity
    import main as main_mod

    store = IdentityStore(db_path=tmp_path / "identity.db")
    set_identity(store)
    if getattr(main_mod, "aggregator", None):
        main_mod.aggregator.set_identity(store)
    yield store
    set_identity(None)


@pytest.fixture(scope="module")
def client():
    """One lifespan for the module — avoids TestClient shutdown hangs."""
    os.environ.pop("OPERATOR_KEY", None)
    from main import app, agent
    from sentinel import set_killed

    set_killed(False)
    if agent:
        agent.quiet = True
    with TestClient(app) as c:
        yield c
    set_killed(False)


def _signup(client, name="Kim", email=None, password="password123"):
    email = email or f"u-{uuid.uuid4().hex[:10]}@example.com"
    r = client.post(
        "/auth/signup",
        json={"display_name": name, "email": email, "password": password},
    )
    assert r.status_code == 200, r.text
    return r.json()["user"]


def test_oauth_state_roundtrip():
    from auth import sign_oauth_state, verify_oauth_state

    state = sign_oauth_state("google")
    assert verify_oauth_state(state, "google")
    assert not verify_oauth_state(state, "facebook")
    assert not verify_oauth_state("bad.state", "google")
    expired = sign_oauth_state("google", ts=int(time.time()) - 9999)
    assert not verify_oauth_state(expired, "google")


def test_google_callback_mocked_exchange(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "test-client")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "test-secret")
    from auth import sign_oauth_state

    state = sign_oauth_state("google")
    fake_info = {"sub": "g-123", "email": "g@example.com", "name": "G User"}

    with patch("auth._exchange_google", return_value=fake_info) as ex:
        r = client.get(
            "/auth/google/callback",
            params={"code": "abc", "state": state},
            follow_redirects=False,
        )
        ex.assert_called_once()
    assert r.status_code in (302, 307)
    assert r.headers.get("location") == "/room"
    assert "djbb_session" in r.cookies


def test_facebook_callback_mocked_exchange(client, monkeypatch):
    monkeypatch.setenv("FACEBOOK_OAUTH_CLIENT_ID", "fb-client")
    monkeypatch.setenv("FACEBOOK_OAUTH_CLIENT_SECRET", "fb-secret")
    from auth import sign_oauth_state

    state = sign_oauth_state("facebook")
    fake_info = {"id": "fb-99", "email": "fb@example.com", "name": "FB Fan"}

    with patch("auth._exchange_facebook", return_value=fake_info):
        r = client.get(
            "/auth/facebook/callback",
            params={"code": "xyz", "state": state},
            follow_redirects=False,
        )
    assert r.status_code in (302, 307)
    assert "djbb_session" in r.cookies


def test_google_callback_rejects_bad_state(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "test-client")
    r = client.get("/auth/google/callback", params={"code": "abc", "state": "nope"})
    assert r.status_code == 400


def test_apple_tiktok_callback_501(client):
    assert client.get("/auth/apple/callback", params={"code": "x", "state": "y"}).status_code == 501
    assert client.get("/auth/tiktok/callback", params={"code": "x", "state": "y"}).status_code == 501


def test_link_code_mint_and_expiry(identity_db, client):
    user = _signup(client)
    r = client.post("/me/link-code")
    assert r.status_code == 200
    data = r.json()
    assert len(data["code"]) == 6
    assert data["expires_at"] > time.time()

    peek = identity_db.peek_link_code(data["code"])
    assert peek and not peek["expired"]
    assert peek["user_id"] == user["id"]

    # Force expiry
    with identity_db._connect() as conn:
        conn.execute(
            "UPDATE link_codes SET expires_at = ? WHERE code = ?",
            (time.time() - 1, data["code"]),
        )
        conn.commit()
    assert identity_db.peek_link_code(data["code"])["expired"] is True
    assert (
        identity_db.consume_link_code(
            data["code"], platform="twitch", handle="KimTwitch"
        )
        is None
    )


def test_identity_resolve_after_link(identity_db, client):
    user = _signup(client, name="BoothKim")
    code = client.post("/me/link-code").json()["code"]
    linked = identity_db.consume_link_code(
        code, platform="twitch", handle="KimTT", platform_user_id="tw-1"
    )
    assert linked["user_id"] == user["id"]
    assert identity_db.resolve("twitch", handle="KimTT") == user["id"]
    assert identity_db.resolve("twitch", platform_user_id="tw-1") == user["id"]
    assert identity_db.display_name_for(user["id"]) == "BoothKim"


def test_aggregator_link_command_and_profile(identity_db, tmp_path):
    from auth import _db, _default_perks, _hash_pw
    from community.aggregator import Aggregator
    from community.schema import ChatMessage, ChatUser
    import time as _t

    conn = _db()
    conn.execute(
        "INSERT INTO users(email, display_name, password_hash, provider, perks, created_at) VALUES (?,?,?,?,?,?)",
        (
            f"link-{uuid.uuid4().hex[:8]}@example.com",
            "LinkedFan",
            _hash_pw("password123"),
            "email",
            json.dumps(_default_perks()),
            int(_t.time()),
        ),
    )
    conn.commit()
    uid = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    conn.close()

    code = identity_db.create_link_code(uid)["code"]
    halo = []
    room = []

    def broadcast(msg):
        room.append(msg)

    class _Agg(Aggregator):
        def publish_halo(self, text, *, name="Halo"):
            halo.append(text)
            return True

    agg = _Agg(room_broadcast=broadcast, identity=identity_db)
    msg = ChatMessage(
        id="twitch:kim:1",
        platform="twitch",
        channel="charles",
        user=ChatUser(platform_user_id="u1", handle="kim_tw", display_name="kim_tw"),
        text=f"!link {code}",
        ts=_t.time(),
    )
    assert agg.ingest(msg) is True
    assert msg.profile_id == uid
    assert any("Booth pass linked" in h for h in halo)

    follow = ChatMessage(
        id="twitch:kim:2",
        platform="twitch",
        channel="charles",
        user=ChatUser(platform_user_id="u1", handle="kim_tw", display_name="kim_tw"),
        text="hey again",
        ts=_t.time(),
    )
    assert agg.ingest(follow) is True
    assert follow.profile_id == uid
    assert follow.user.display_name == "LinkedFan"


def test_memory_merge_across_linked_handles(tmp_path):
    from memory import ViewerMemory

    mem = ViewerMemory(db_path=tmp_path / "halo.db")
    mem.touch("twitch", "Kim")
    with mem._connect() as conn:
        conn.execute(
            "UPDATE viewers SET last_seen = ?, visits = 3, last_request = ? WHERE platform = ? AND handle = ?",
            (time.time() - 86400 * 2, "mad world", "twitch", "kim"),
        )
        conn.commit()

    # Bind twitch handle to booth-pass, keep prior-day last_seen, then visit on YT
    mem.attach_profile("twitch", "Kim", 7)
    with mem._connect() as conn:
        conn.execute(
            "UPDATE viewers SET last_seen = ?, visits = 3 WHERE profile_id = ?",
            (time.time() - 86400 * 2, "7"),
        )
        conn.commit()
    rec, returning = mem.touch("youtube", "KimYT", profile_id=7)
    assert returning is True
    assert rec.visits >= 4
    assert rec.profile_id == "7"

    mem.remember_request("youtube", "KimYT", "bloodrave", profile_id=7)
    merged = mem.get_by_profile(7)
    assert merged is not None
    assert merged.visits >= 4
    assert merged.last_request == "bloodrave"

    via_twitch = mem.get("twitch", "Kim", profile_id=7)
    assert via_twitch is not None
    assert via_twitch.visits >= 4
    assert via_twitch.last_request == "bloodrave"


def test_me_profile_and_unlink(client, identity_db):
    user = _signup(client)
    code = client.post("/me/link-code").json()["code"]
    identity_db.consume_link_code(code, platform="twitch", handle="prof_tw")
    r = client.get("/me/profile")
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["id"] == user["id"]
    assert any(l["handle"] == "prof_tw" for l in body["linked_handles"])

    un = client.post("/me/unlink", json={"platform": "twitch", "handle": "prof_tw"})
    assert un.status_code == 200
    assert un.json()["ok"] is True
    assert un.json()["linked_handles"] == []


def test_request_priority_perk(client, tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    email = f"prio-{uuid.uuid4().hex[:8]}@example.com"
    user = _signup(client, email=email, name="Prio")
    # Flip perk in DB
    from auth import DB_PATH
    import sqlite3

    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute("SELECT perks FROM users WHERE id = ?", (user["id"],)).fetchone()
    perks = json.loads(row[0])
    perks["request_priority"] = True
    conn.execute("UPDATE users SET perks = ? WHERE id = ?", (json.dumps(perks), user["id"]))
    conn.commit()
    conn.close()

    # Seed a normal request first (anonymous / logged out)
    client.post("/auth/logout")
    from main import state

    state["queue"].clear()
    r0 = client.post(
        "/request",
        json={"query": "slow song", "requester": "Anon", "via": "Web Hub", "silent": True},
    )
    assert r0.status_code == 200
    assert not r0.json()["item"].get("priority")

    # Log back in with request_priority perk
    login = client.post("/auth/login", json={"email": email, "password": "password123"})
    assert login.status_code == 200
    r1 = client.post(
        "/request",
        json={"query": "priority track", "requester": "X", "via": "Web Hub", "silent": True},
    )
    assert r1.status_code == 200
    assert r1.json()["item"]["priority"] is True

    assert state["queue"][0]["priority"] is True
    assert state["queue"][0]["query"] == "priority track"


def test_room_chat_uses_signed_in_profile(client, identity_db):
    import main as main_mod

    user = _signup(client, name="RoomUser")
    # Avoid room_hub broadcast deadlock: drop live subscribers for this call
    if main_mod.agent:
        main_mod.agent.quiet = True
    prev_clients = list(main_mod.room_hub.clients)
    main_mod.room_hub.clients.clear()
    try:
        r = client.post(
            "/chat",
            json={"author": "IgnoredNick", "message": "hello from room", "platform": "room"},
        )
    finally:
        main_mod.room_hub.clients.extend(prev_clients)
    assert r.status_code == 200
    assert r.json().get("profile_id") == user["id"]
