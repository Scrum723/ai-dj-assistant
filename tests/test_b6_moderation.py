"""B6: Unified moderation and ban sync."""

from __future__ import annotations

import time
from typing import Optional

import pytest
from fastapi.testclient import TestClient

from community.adapters.base import ChatAdapter
from community.aggregator import Aggregator
from community.identity import IdentityStore
from community.mod import CommunityMod
from community.schema import ChatMessage, ChatUser, SupportInfo
from moderation import reset_rate_limits


class FakeAdapter(ChatAdapter):
    name = "fake"
    can_moderate = False

    def __init__(self, channel: str = "test", *, can_moderate: bool = False):
        super().__init__(channel=channel)
        self.can_moderate = can_moderate
        self.bans: list[tuple[str, Optional[int]]] = []

    def start(self, emit):
        self._emit = emit
        self._running = True
        self._connected = True

    def stop(self):
        self._running = False
        self._connected = False

    def ban(self, handle: str, seconds: Optional[int] = None) -> None:
        if not self.can_moderate:
            raise NotImplementedError("cannot moderate")
        self.bans.append((handle, seconds))


def _msg(
    platform: str,
    mid: str,
    handle: str,
    text: str,
    *,
    profile_id=None,
    kind: str = "chat",
) -> ChatMessage:
    return ChatMessage(
        id=f"{platform}:{mid}",
        platform=platform,
        channel="test",
        user=ChatUser(platform_user_id=handle, handle=handle, display_name=handle),
        text=text,
        kind=kind,
        profile_id=profile_id,
        ts=time.time(),
    )


@pytest.fixture
def clock():
    t = {"now": 1_700_000_000.0}

    def now():
        return t["now"]

    now.advance = lambda s: t.__setitem__("now", t["now"] + s)  # type: ignore[attr-defined]
    now.set = lambda v: t.__setitem__("now", float(v))  # type: ignore[attr-defined]
    return now


@pytest.fixture
def mod(tmp_path, clock):
    return CommunityMod(db_path=tmp_path / "community.db", clock=clock)


def test_ban_by_profile_hides_all_linked_handles(tmp_path, clock):
    reset_rate_limits()
    identity = IdentityStore(db_path=tmp_path / "identity.db")
    # Link same booth pass across platforms
    with identity._connect() as conn:
        conn.execute(
            "INSERT INTO linked_handles(user_id, platform, platform_user_id, handle, linked_at) VALUES (?,?,?,?,?)",
            (42, "twitch", "t1", "AliceTW", clock()),
        )
        conn.execute(
            "INSERT INTO linked_handles(user_id, platform, platform_user_id, handle, linked_at) VALUES (?,?,?,?,?)",
            (42, "youtube", "y1", "AliceYT", clock()),
        )
        conn.commit()

    m = CommunityMod(db_path=tmp_path / "community.db", clock=clock, identity=identity)
    broadcasts = []
    accepted = []
    agg = Aggregator(
        cohost_ingest=lambda *a, **k: accepted.append((a, k)),
        room_broadcast=lambda p: broadcasts.append(p),
        get_settings=lambda: {},
        identity=identity,
        mod=m,
    )

    assert agg.ingest(_msg("twitch", "1", "AliceTW", "hi before")) is True

    m.ban(target_profile_id=42, reason="spam", by_name="Charles")

    assert agg.ingest(_msg("twitch", "2", "AliceTW", "hi twitch")) is False
    assert agg.ingest(_msg("youtube", "3", "AliceYT", "hi youtube")) is False
    assert agg.ingest(_msg("room", "4", "Other", "ok")) is True

    log = m.list_log()
    assert log and log[0]["action"] == "ban"
    assert log[0]["by_name"] == "Charles"
    assert log[0]["reason"] == "spam"
    assert log[0]["target_profile_id"] == 42


def test_timeout_expires_on_schedule(mod, clock):
    mod.timeout(
        seconds=60,
        platform="twitch",
        handle="Troublemaker",
        reason="slow down",
        by_name="mod",
    )
    assert mod.is_banned(platform="twitch", handle="Troublemaker") is True
    clock.advance(61)
    assert mod.is_banned(platform="twitch", handle="Troublemaker") is False
    assert mod.active_bans() == []
    actions = [r["action"] for r in mod.list_log()]
    assert "timeout" in actions


def test_push_adapters_only_when_enabled(tmp_path, clock):
    tw = FakeAdapter(can_moderate=True)
    tw.name = "twitch"
    yt = FakeAdapter(can_moderate=True)
    yt.name = "youtube"
    kick = FakeAdapter(can_moderate=False)
    kick.name = "kick"

    identity = IdentityStore(db_path=tmp_path / "identity.db")
    with identity._connect() as conn:
        conn.execute(
            "INSERT INTO linked_handles(user_id, platform, platform_user_id, handle, linked_at) VALUES (?,?,?,?,?)",
            (7, "twitch", "t", "Bob", clock()),
        )
        conn.execute(
            "INSERT INTO linked_handles(user_id, platform, platform_user_id, handle, linked_at) VALUES (?,?,?,?,?)",
            (7, "youtube", "y", "BobYT", clock()),
        )
        conn.commit()

    settings = {"community": {"push_bans": False}}
    m = CommunityMod(
        db_path=tmp_path / "community.db",
        clock=clock,
        identity=identity,
        get_settings=lambda: settings,
        get_adapters=lambda: {"twitch": tw, "youtube": yt, "kick": kick},
    )

    out = m.timeout(seconds=120, target_profile_id=7, reason="to", by_name="op")
    assert out["push"]["enabled"] is False
    assert tw.bans == [] and yt.bans == []

    settings["community"]["push_bans"] = True
    out2 = m.ban(target_profile_id=7, reason="perm", by_name="op")
    assert out2["push"]["enabled"] is True
    assert ("Bob", None) in tw.bans
    assert ("BobYT", None) in yt.bans
    # kick cannot_moderate — listed as failure, not called
    kick_results = [r for r in out2["push"]["results"] if r["platform"] == "kick"]
    assert not kick_results  # no kick link
    assert kick.bans == []


def test_delete_broadcasts_and_logs(mod):
    events = []
    mod.set_broadcast(lambda p: events.append(p))
    res = mod.delete_message("twitch:abc", reason="offtopic", by_name="Charles")
    assert res["ok"] is True
    assert events == [
        {"type": "delete-message", "payload": {"id": "twitch:abc", "reason": "offtopic", "by": "Charles"}}
    ]
    assert mod.list_log()[0]["action"] == "delete"
    assert mod.list_log()[0]["message_id"] == "twitch:abc"


def test_empty_support_kind_passes_aggregator():
    reset_rate_limits()
    ingested = []
    agg = Aggregator(
        cohost_ingest=lambda author, text, **kw: ingested.append({"author": author, "text": text, **kw}),
        get_settings=lambda: {},
    )
    msg = ChatMessage(
        id="youtube:sc1",
        platform="youtube",
        channel="live",
        user=ChatUser(platform_user_id="u1", handle="Patron", display_name="Patron"),
        text="",
        kind="super_chat",
        support=SupportInfo(amount=5.0, currency="USD"),
        ts=time.time(),
    )
    assert agg.ingest(msg) is True
    assert ingested and ingested[0]["event_kind"] == "super_chat"


def test_mod_api_routes(tmp_path, monkeypatch):
    """Local booth (no OPERATOR_KEY) can hit mod endpoints; log is operator-gated same way."""
    monkeypatch.delenv("OPERATOR_KEY", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from main import app

    with TestClient(app) as client:
        r = client.get("/community/mod/me")
        assert r.status_code == 200
        assert r.json().get("is_mod") is True

        ban = client.post(
            "/community/mod/ban",
            json={"platform": "twitch", "handle": "SpamBot", "reason": "bots"},
        )
        assert ban.status_code == 200
        assert ban.json().get("ok") is True

        to = client.post(
            "/community/mod/timeout",
            json={"platform": "twitch", "handle": "Noisy", "seconds": 30, "reason": "quiet"},
        )
        assert to.status_code == 200
        assert to.json().get("until")

        deleted = client.post(
            "/community/mod/delete",
            json={"message_id": "room:x:1", "reason": "clean"},
        )
        assert deleted.status_code == 200

        un = client.post(
            "/community/mod/unban",
            json={"platform": "twitch", "handle": "SpamBot", "reason": "forgiven"},
        )
        assert un.status_code == 200

        log = client.get("/community/mod/log")
        assert log.status_code == 200
        actions = {row["action"] for row in log.json()["log"]}
        assert "ban" in actions and "delete" in actions and "unban" in actions
