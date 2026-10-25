"""A7: Greeting SM, segment scheduler, DJ gating, in-joke frequency caps."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import pytest

from memory import (
    GreetingShowState,
    ViewerMemory,
    genres_from_request,
    parse_where_from,
)
from segments import SegmentScheduler


class FakeClock:
    def __init__(self, start: float = 1_700_000_000.0):
        self.t = float(start)

    def __call__(self) -> float:
        return self.t

    def advance(self, sec: float) -> None:
        self.t += float(sec)

    def set(self, ts: float) -> None:
        self.t = float(ts)


def test_greeting_sm_first_returning_milestone_once():
    sm = GreetingShowState()
    key = sm.key("twitch", "Fan")
    assert sm.next_stage(visits=1, is_new_day_return=False, key=key) == "first_time"
    sm.mark(key, "first_time")
    assert sm.next_stage(visits=1, is_new_day_return=False, key=key) is None

    assert sm.next_stage(visits=2, is_new_day_return=True, key=key) == "returning"
    sm.mark(key, "returning")
    assert sm.next_stage(visits=2, is_new_day_return=True, key=key) is None

    assert sm.next_stage(visits=5, is_new_day_return=True, key=key) == "milestone"
    sm.mark(key, "milestone")
    assert sm.next_stage(visits=5, is_new_day_return=True, key=key) is None

    # New show resets
    sm.reset_show()
    assert sm.next_stage(visits=5, is_new_day_return=True, key=key) == "milestone"


def test_greeting_sm_profile_key():
    sm = GreetingShowState()
    k1 = sm.key("twitch", "a", profile_id=9)
    k2 = sm.key("youtube", "b", profile_id=9)
    assert k1 == k2 == "profile:9"


def test_parse_where_from_and_genres():
    assert parse_where_from("hey I'm from Buffalo tonight") == "Buffalo"
    assert parse_where_from("listening from NYC") == "NYC"
    assert parse_where_from("this slaps") is None
    assert "dnb" in genres_from_request("play some dnb bloodrave")
    assert "house" in genres_from_request("queue deep house")


def test_memory_where_from_and_genres(tmp_path):
    mem = ViewerMemory(db_path=tmp_path / "halo.db")
    mem.set_where_from("chat", "Fan", "Buffalo")
    mem.remember_request("chat", "Fan", "play techno banger")
    got = mem.get("chat", "Fan")
    assert got.where_from == "Buffalo"
    assert "techno" in got.genres_list()
    assert "listening from Buffalo" in got.memory_line()
    assert "likes techno" in got.memory_line()
    listed = mem.list_listening_from()
    assert listed and listed[0]["where_from"] == "Buffalo"


def test_segment_scheduler_fake_clock_and_dj_gating():
    # :10 so at_minute=0 builtins from merge do not fire
    clock = FakeClock(start=datetime(2026, 1, 1, 12, 10, 0, tzinfo=timezone.utc).timestamp())
    sched = SegmentScheduler(clock=clock)
    configs = [
        {"name": "roll_call", "every_min": 10, "enabled": True, "dj_mode_ok": True},
        {"name": "guess_the_bpm", "every_min": 10, "enabled": True, "dj_mode_ok": False},
    ]
    # Arm every_min — first tick records last, no fire
    assert sched.tick(configs, dj_mode=False, now=clock()) is None
    clock.advance(10 * 60)
    ev = sched.tick(configs, dj_mode=False, now=clock(), bpm=128)
    assert ev is not None
    assert ev.name in {"roll_call", "guess_the_bpm"}

    # DJ mode: only dj_mode_ok segments
    sched2 = SegmentScheduler(clock=clock)
    assert sched2.tick(configs, dj_mode=True, now=clock()) is None
    clock.advance(10 * 60)
    ev2 = sched2.tick(configs, dj_mode=True, now=clock(), bpm=120)
    assert ev2 is not None
    assert ev2.name == "roll_call"

    # track_id always ok in DJ mode by default
    tid = sched2.on_transition(configs, title="Mad World", dj_mode=True, now=clock())
    assert tid is not None
    assert tid.name == "track_id"
    assert "Mad World" in tid.line


def test_segment_at_minute_once_per_hour():
    # 12:55 UTC
    start = datetime(2026, 1, 1, 12, 55, 0, tzinfo=timezone.utc).timestamp()
    clock = FakeClock(start=start)
    sched = SegmentScheduler(clock=clock)
    configs = [{"name": "last_call", "at_minute": 55, "enabled": True, "dj_mode_ok": False}]
    ev = sched.tick(configs, dj_mode=False, now=clock())
    assert ev is not None and ev.name == "last_call"
    # Same minute again — no re-fire
    assert sched.tick(configs, dj_mode=False, now=clock()) is None
    clock.advance(60)  # 12:56
    assert sched.tick(configs, dj_mode=False, now=clock()) is None


def test_cohost_greeting_once_per_show(agent, spoken, tmp_memory):
    agent._clock = FakeClock(1_700_000_000.0)
    agent.ingest("Newbie", "hey there", platform="room")
    item = agent._inbox.get_nowait()
    assert item.get("_greet_stage") == "first_time"
    asyncio.run(
        agent._handle_chat(
            item,
            autonomy=70,
            dj_mode=False,
            caps=agent._cfg()["halo"]["capabilities"],
            energy="eco",
        )
    )
    assert agent.greets == 1
    assert any("Tip:" in s["text"] or "welcome" in s["text"].lower() for s in spoken)

    spoken.clear()
    # Same show — no second first_time
    agent.ingest("Newbie", "hey again", platform="room")
    item2 = agent._inbox.get_nowait()
    assert item2.get("_greet_stage") in (None, "")
    asyncio.run(
        agent._handle_chat(
            item2,
            autonomy=100,
            dj_mode=False,
            caps=agent._cfg()["halo"]["capabilities"],
            energy="eco",
        )
    )
    assert agent.greets == 1


def test_cohost_milestone_greeting(agent, spoken, tmp_memory):
    tmp_memory.touch("chat", "Regular")
    with tmp_memory._connect() as conn:
        conn.execute(
            "UPDATE viewers SET visits = 4, last_seen = ? WHERE platform = ? AND handle = ?",
            (time.time() - 86400 * 2, "chat", "regular"),
        )
        conn.commit()
    agent._greet_state.reset_show()
    agent.ingest("Regular", "hey", platform="chat")
    item = agent._inbox.get_nowait()
    assert item.get("_greet_stage") == "milestone" or item.get("_kind") == "milestone"
    item["_kind"] = "milestone"
    item["_greet_stage"] = "milestone"
    asyncio.run(
        agent._handle_chat(
            item,
            autonomy=70,
            dj_mode=False,
            caps=agent._cfg()["halo"]["capabilities"],
            energy="eco",
        )
    )
    assert agent.greets == 1
    assert any("5" in s["text"] or "visit" in s["text"].lower() for s in spoken)


def test_in_joke_frequency_cap(agent):
    clock = FakeClock(1_000_000.0)
    agent._clock = clock
    caps = dict(agent.get_settings()["halo"]["capabilities"])

    def settings():
        return {
            "demo": False,
            "autonomy": 70,
            "halo": {
                "name": "Halo",
                "persona_preset": "hype",
                "dj_mode": False,
                "dj_mode_silent": False,
                "live": True,
                "capabilities": caps,
            },
            "environment": {"energy_mode": "balanced", "distraction": 30},
            "community": {"name": "Y'all Squad", "in_jokes": ["Lake effect energy"]},
        }

    agent.get_settings = settings
    j1 = agent._peek_in_joke()
    assert j1 == "Lake effect energy"
    agent._mark_in_joke()
    assert agent._peek_in_joke() is None
    clock.advance(15 * 60)
    assert agent._peek_in_joke() == "Lake effect energy"


def test_cohost_segment_tick_dj_gate(agent, spoken):
    clock = FakeClock(datetime(2026, 1, 1, 12, 10, 0, tzinfo=timezone.utc).timestamp())
    agent._clock = clock
    agent._segments = SegmentScheduler(clock=clock)
    agent._last_reply_at = 0.0
    base_halo = dict(agent.get_settings().get("halo") or {})

    def settings():
        return {
            "demo": False,
            "autonomy": 70,
            "halo": {
                **base_halo,
                "dj_mode": True,
                "dj_mode_silent": False,
                "live": True,
                "min_reply_sec": 0,
                "max_replies_per_min": 20,
                "segments": [
                    {"name": "roll_call", "every_min": 5, "enabled": True, "dj_mode_ok": True},
                    {"name": "guess_the_bpm", "every_min": 5, "enabled": True, "dj_mode_ok": False},
                ],
            },
            "environment": {"energy_mode": "balanced", "distraction": 30},
            "community": {"name": "", "in_jokes": []},
        }

    agent.get_settings = settings
    # Arm
    asyncio.run(agent._tick())
    spoken.clear()
    clock.advance(5 * 60)
    agent._last_reply_at = 0.0
    asyncio.run(agent._tick())
    assert spoken
    assert all("bpm" not in s["text"].lower() for s in spoken)
    assert any("roll call" in s["text"].lower() for s in spoken)


def test_listening_from_list(agent, tmp_memory):
    tmp_memory.set_where_from("room", "Kim", "Toronto")
    rows = agent._listening_from_list()
    assert any(r["where_from"] == "Toronto" for r in rows)
    assert agent.status().get("listening_from") is not None
