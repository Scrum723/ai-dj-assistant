"""A5: Priority queue, expiry, cooldown, waiting-room gating (fake clock)."""

from __future__ import annotations

import asyncio
import time

import pytest

from conftest import UnavailableLLM
from halo_queue import PriorityQueue


class FakeClock:
    def __init__(self, start: float = 1_000_000.0):
        self.t = float(start)

    def __call__(self) -> float:
        return self.t

    def advance(self, sec: float) -> None:
        self.t += float(sec)


def test_queue_ordering_supporter_before_regular():
    clock = FakeClock()
    q = PriorityQueue(clock=clock, task_ttl_sec=120)
    q.push(kind="regular", target="A", platform="twitch", payload={"message": "hi"}, now=clock())
    clock.advance(0.1)
    q.push(kind="supporter", target="VIP", platform="youtube", payload={"message": "sc"}, now=clock())
    clock.advance(0.1)
    q.push(kind="mention", target="B", platform="room", payload={"message": "halo"}, now=clock())
    first = q.pop(now=clock())
    assert first is not None
    assert first.target == "VIP"
    assert first.kind == "supporter"
    second = q.pop(now=clock())
    assert second.target == "B"
    third = q.pop(now=clock())
    assert third.target == "A"


def test_low_priority_expires_after_ttl():
    clock = FakeClock()
    q = PriorityQueue(clock=clock, task_ttl_sec=120)
    q.push(kind="regular", target="Slow", platform="chat", payload={}, now=clock())
    q.push(kind="supporter", target="Paid", platform="youtube", payload={}, now=clock())
    clock.advance(121)
    expired = q.expire(now=clock())
    assert any(t.target == "Slow" for t in expired)
    assert q.length() == 1
    assert q.peek().target == "Paid"


def test_ack_after_20s():
    clock = FakeClock()
    q = PriorityQueue(clock=clock)
    t = q.push(kind="mention", target="Waiter", platform="twitch", payload={}, now=clock())
    assert q.tasks_needing_ack(now=clock(), wait_sec=20) == []
    clock.advance(21)
    needing = q.tasks_needing_ack(now=clock(), wait_sec=20)
    assert len(needing) == 1
    assert needing[0][0].task_id == t.task_id
    assert needing[0][1] == 1
    q.mark_acked(t.task_id)
    assert q.tasks_needing_ack(now=clock(), wait_sec=20) == []


def test_cohost_priority_under_load(agent, spoken):
    agent._llm = UnavailableLLM()
    clock = FakeClock()
    agent._clock = clock
    agent.queue = PriorityQueue(clock=clock, task_ttl_sec=120)
    agent._last_reply_at = 0.0
    agent._last_idle = clock()
    agent.queue.clear()

    for i in range(10):
        agent.ingest(f"Fan{i}", f"chatter {i}", platform="room")
        clock.advance(0.01)
    agent.ingest("Tipper", "thanks for the mix", platform="youtube", event_kind="super_chat")
    # A6 burst debounce (BURST_QUIET_SEC) before supporter task is enqueued
    clock.advance(1.6)
    asyncio.run(agent._tick())
    assert spoken
    assert spoken[0]["target"] == "Tipper"


def test_per_user_cooldown_with_fake_clock(agent, spoken):
    agent._llm = UnavailableLLM()
    clock = FakeClock()
    agent._clock = clock
    agent.queue = PriorityQueue(clock=clock)
    base = agent.get_settings()

    def gs():
        return {
            **base,
            "halo": {
                **base["halo"],
                "live": True,
                "min_reply_sec": 0,
                "max_replies_per_min": 20,
                "user_cooldown_sec": 30,
            },
        }

    agent.get_settings = gs
    agent._last_reply_at = 0.0
    agent._last_idle = clock()
    agent._last_reply_user["sam"] = clock()
    agent.ingest("Sam", "hey halo again", platform="twitch")
    asyncio.run(agent._tick())
    assert spoken == []
    assert agent.queue.length() == 1

    clock.advance(31)
    agent._last_reply_at = 0.0
    spoken.clear()
    asyncio.run(agent._tick())
    assert len(spoken) == 1
    assert spoken[0]["target"] == "Sam"


def test_offline_waiting_room_cooldown_and_no_live_claim(agent, spoken):
    agent._llm = UnavailableLLM()
    clock = FakeClock()
    agent._clock = clock
    agent.queue = PriorityQueue(clock=clock)
    base = agent.get_settings()

    def gs():
        return {
            **base,
            "halo": {
                **base["halo"],
                "live": False,
                "min_reply_sec": 0,
                "max_replies_per_min": 6,
                "user_cooldown_sec": 20,
                "next_show": "Friday 9pm",
                "schedule": "weekends",
                "last_set": "Synthetic Halo night",
            },
            "environment": {"energy_mode": "balanced", "distraction": 30, "vibe": "night"},
        }

    agent.get_settings = gs
    assert agent._waiting_room() is True
    assert agent._user_cooldown_sec() >= 30

    agent._last_reply_user["alex"] = clock()
    agent._last_reply_at = 0.0
    agent.ingest("Alex", "hey halo are we live?", platform="room")
    asyncio.run(agent._tick())
    assert spoken == [], "offline: 30s per-person cooldown"

    clock.advance(31)
    agent._last_reply_at = 0.0
    spoken.clear()
    asyncio.run(agent._tick())
    assert len(spoken) == 1
    text = spoken[-1]["text"].lower()
    assert "charles is live" not in text
    assert "we're live right now" not in text

    spoken.clear()
    agent._last_idle = 0.0
    agent.queue.clear()
    agent._idle_line(70, agent._cfg()["halo"]["capabilities"])
    assert spoken
    idle = spoken[-1]["text"].lower()
    assert "charles is live" not in idle
    assert any(
        s in idle
        for s in (
            "waiting room",
            "next show",
            "not live",
            "isn't live",
            "last set",
            "last spin",
            "schedule",
            "next one",
            "next time",
        )
    )


def test_state_exposes_queue_fields(agent):
    agent.queue.clear()
    agent.ingest("A", "hi", platform="twitch")
    agent.ingest("B", "hey halo", platform="youtube")
    st = agent.status()
    assert st["queue_length"] >= 2
    assert isinstance(st["queue"], list)
    assert "queue_positions" in st
    assert st["live"] is True
    assert st["waiting_room"] is False


def test_aggregator_operator_skips_rate():
    from community.aggregator import Aggregator
    from community.schema import ChatMessage, ChatUser
    from moderation import reset_rate_limits

    reset_rate_limits()
    ingested = []

    def cohost_ingest(author, text, platform="chat", from_aggregator=False, **kwargs):
        ingested.append((author, text, platform))

    def get_settings():
        return {"halo": {"operator_handles": ["charles"]}, "moderation": {"blocklist": []}}

    agg = Aggregator(cohost_ingest=cohost_ingest, get_settings=get_settings)
    for i in range(15):
        msg = ChatMessage(
            id=f"op-{i}",
            platform="twitch",
            channel="charles",
            user=ChatUser(handle="charles", display_name="charles"),
            text=f"ping {i}",
            kind="chat",
            ts=time.time() + i,
        )
        assert agg.ingest(msg) is True, f"operator drop at {i}"
    assert len(ingested) == 15
