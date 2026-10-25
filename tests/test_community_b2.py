"""B2: community store, history pagination, presence expiry, APIs."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from community.aggregator import Aggregator
from community.schema import ChatMessage, ChatUser
from community.store import CommunityStore
from moderation import reset_rate_limits


class FakeClock:
    def __init__(self, start: float = 1_700_000_000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _msg(platform: str, mid: str, handle: str, text: str, ts: float | None = None) -> ChatMessage:
    return ChatMessage(
        id=f"{platform}:{mid}",
        platform=platform,
        channel="test",
        user=ChatUser(platform_user_id=handle, handle=handle, display_name=handle),
        text=text,
        ts=ts if ts is not None else time.time(),
    )


def test_store_insert_query_and_retention(tmp_path):
    clock = FakeClock()
    store = CommunityStore(tmp_path / "community.db", retention_days=2, clock=clock)

    m1 = _msg("youtube", "a", "Raj", "one", ts=clock.t)
    clock.advance(10)
    m2 = _msg("twitch", "b", "Kim", "two", ts=clock.t)
    store.insert_message(m1)
    store.insert_message(m2)

    hist = store.history(limit=10)
    assert len(hist) == 2
    assert hist[0]["id"] == m1.id
    assert hist[1]["id"] == m2.id
    assert "raw" not in hist[0]

    # Pagination: before newest → only older
    page = store.history(before=m2.ts, limit=10)
    assert [h["id"] for h in page] == [m1.id]

    # Retention: age past retention_days
    clock.advance(3 * 86400)
    deleted = store.prune()
    assert deleted >= 2
    assert store.history(limit=10) == []


def test_presence_expiry_on_fake_clock(tmp_path):
    clock = FakeClock()
    store = CommunityStore(tmp_path / "community.db", clock=clock)
    store.touch_presence("youtube", "Raj")
    store.touch_presence("twitch", "Kim")
    p = store.presence(room_sockets=2, now=clock.t)
    assert p["counts"]["youtube"] == 1
    assert p["counts"]["twitch"] == 1
    assert p["counts"]["room"] == 2
    assert p["total"] == 4

    clock.advance(5 * 60 + 1)
    p2 = store.presence(room_sockets=1, now=clock.t)
    assert p2["counts"].get("youtube", 0) == 0
    assert p2["counts"].get("twitch", 0) == 0
    assert p2["counts"]["room"] == 1
    assert p2["users"] == []


def test_history_pagination_via_api(tmp_path, monkeypatch):
    reset_rate_limits()
    import main

    store = CommunityStore(tmp_path / "community.db")
    base = 1_700_000_100.0
    for i in range(5):
        store.insert_message(_msg("room", str(i), "Fan", f"msg-{i}", ts=base + i))

    with TestClient(main.app) as client:
        # Lifespan creates its own store — swap after startup
        monkeypatch.setattr(main, "community_store", store)
        if main.aggregator:
            main.aggregator.set_store(store)

        r = client.get("/community/history", params={"limit": 3})
        assert r.status_code == 200
        msgs = r.json()["messages"]
        assert len(msgs) == 3
        assert msgs[0]["text"] == "msg-2"
        assert msgs[-1]["text"] == "msg-4"
        assert all("raw" not in m for m in msgs)

        before = msgs[0]["ts"]
        r2 = client.get("/community/history", params={"before": before, "limit": 10})
        older = r2.json()["messages"]
        assert [m["text"] for m in older] == ["msg-0", "msg-1"]


def test_aggregator_persists_to_store(tmp_path):
    reset_rate_limits()
    store = CommunityStore(tmp_path / "community.db")
    roomed = []
    agg = Aggregator(
        room_broadcast=lambda m: roomed.append(m),
        get_settings=lambda: {"moderation": {"blocklist": []}},
        store=store,
    )
    msg = _msg("youtube", "persist-1", "Raj", "saved")
    assert agg.ingest(msg) is True
    hist = store.history(limit=10)
    assert len(hist) == 1
    assert hist[0]["id"] == msg.id
    assert hist[0]["platform"] == "youtube"


def test_presence_endpoint(tmp_path, monkeypatch):
    import main

    store = CommunityStore(tmp_path / "p.db")
    store.touch_presence("twitch", "Kim")

    with TestClient(main.app) as client:
        monkeypatch.setattr(main, "community_store", store)
        r = client.get("/community/presence")
        assert r.status_code == 200
        body = r.json()
        assert "counts" in body
        assert body["counts"].get("twitch") == 1
        assert "room" in body["counts"]
