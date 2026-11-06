"""V3: Chief track awareness — announce, !song, history."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"


class FakeClock:
    def __init__(self, start: float = 1_700_000_000.0):
        self.t = float(start)

    def __call__(self) -> float:
        return self.t

    def advance(self, sec: float) -> float:
        self.t += float(sec)
        return self.t


@pytest.fixture
def announce_agent(tmp_memory, spoken, noted, monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from cohost import CohostAgent
    from conftest import FakeLLM, UnavailableLLM

    llm = UnavailableLLM()
    settings = {
        "demo": False,
        "autonomy": 70,
        "halo": {
            "name": "Chief",
            "persona_preset": "hype",
            "persona": "",
            "dj_mode": False,
            "dj_mode_silent": False,
            "live": True,
            "announce_tracks": True,
            "announce_min_gap_sec": 90,
            "announce_tts": False,
            "min_reply_sec": 1,
            "max_replies_per_min": 20,
            "user_cooldown_sec": 1,
            "task_ttl_sec": 120,
            "operator_handles": ["charles"],
            "capabilities": {
                "greet": True,
                "questions": True,
                "hype": True,
                "requests": True,
                "idle_talk": False,
                "introduce_fans": True,
            },
            "segments": [{"name": "track_id", "enabled": True, "dj_mode_ok": True}],
        },
        "environment": {"energy_mode": "eco", "distraction": 30, "vibe": "night"},
    }

    def get_snapshot():
        return {"current_track": {}, "queue": []}

    a = CohostAgent(
        get_snapshot=get_snapshot,
        search_tracks=lambda q: [],
        enqueue_request=lambda **k: {"status": "miss"},
        speak=lambda text, target=None, mood="talk": spoken.append(
            {"text": text, "target": target, "mood": mood}
        ),
        note_chat=lambda item: noted.append(item),
        get_autonomy=lambda: 70,
        get_settings=lambda: settings,
        memory=tmp_memory,
        llm=llm,
    )
    clock = FakeClock()
    a._clock = clock
    a._intro_done = True
    return a, settings, clock, spoken, noted


def test_classify_track_id_intents(announce_agent):
    agent, *_ = announce_agent
    for msg in (
        "!song",
        "!track",
        "!id",
        "what song is this",
        "what's playing",
        "track id",
        "song name",
        "now playing",
    ):
        assert agent._classify("Fan", msg) == "track_id", msg


def test_song_returns_current_without_llm(announce_agent):
    agent, settings, clock, spoken, noted = announce_agent
    from conftest import FakeLLM

    fake = FakeLLM()
    agent._llm = fake
    agent._current_np = {
        "artist": "Lane 8",
        "title": "Fingerprint",
        "bpm": 124,
        "key": "8A",
    }
    agent.ingest("Fan", "!song", platform="room")
    task = agent.queue.pop(now=clock())
    assert task is not None and task.kind == "track_id"
    item = agent._item_from_task(task)
    asyncio.run(
        agent._handle_chat(
            item,
            autonomy=70,
            dj_mode=False,
            caps=settings["halo"]["capabilities"],
            energy="eco",
        )
    )
    assert fake.calls == []
    assert spoken  # track_id answers still use speak
    assert "Fingerprint" in spoken[-1]["text"]
    assert "Lane 8" in spoken[-1]["text"]
    assert "124" in spoken[-1]["text"]


def test_song_no_track(announce_agent):
    agent, settings, clock, spoken, noted = announce_agent
    agent._current_np = None
    agent.ingest("Fan", "what's playing", platform="room")
    task = agent.queue.pop(now=clock())
    item = agent._item_from_task(task)
    asyncio.run(
        agent._handle_chat(
            item,
            autonomy=70,
            dj_mode=False,
            caps=settings["halo"]["capabilities"],
            energy="eco",
        )
    )
    assert spoken
    assert "Not sure yet" in spoken[-1]["text"]


def test_fake_track_change_announces_once(announce_agent):
    agent, settings, clock, spoken, noted = announce_agent
    room = []
    agent.set_publish_halo_hook(lambda name, text: room.append({"name": name, "text": text}))

    agent.on_track_change({"artist": "Amelie Lens", "title": "Hypnotized", "bpm": 150})
    assert agent._pending_track_announce is not None
    asyncio.run(agent._tick())
    assert len(room) == 1
    assert "Hypnotized" in room[0]["text"]
    assert "Amelie Lens" in room[0]["text"]
    assert spoken == []  # announce_tts default false


def test_two_changes_within_gap_one_message(announce_agent):
    agent, settings, clock, spoken, noted = announce_agent
    room = []
    agent.set_publish_halo_hook(lambda name, text: room.append({"name": name, "text": text}))

    agent.on_track_change({"artist": "A", "title": "One"})
    asyncio.run(agent._tick())
    assert len(room) == 1

    clock.advance(30)  # within announce_min_gap_sec=90
    agent.on_track_change({"artist": "B", "title": "Two"})
    assert agent._pending_track_announce is None
    asyncio.run(agent._tick())
    assert len(room) == 1


def test_skip_within_20s_of_change(announce_agent):
    agent, settings, clock, spoken, noted = announce_agent
    room = []
    agent.set_publish_halo_hook(lambda name, text: room.append({"name": name, "text": text}))
    settings["halo"]["announce_min_gap_sec"] = 5

    agent.on_track_change({"artist": "A", "title": "One"})
    asyncio.run(agent._tick())
    clock.advance(10)  # < 20s hard skip
    agent.on_track_change({"artist": "B", "title": "Two"})
    assert agent._pending_track_announce is None
    asyncio.run(agent._tick())
    assert len(room) == 1


def test_dj_mode_no_announce(announce_agent):
    agent, settings, clock, spoken, noted = announce_agent
    room = []
    agent.set_publish_halo_hook(lambda name, text: room.append({"name": name, "text": text}))
    settings["halo"]["dj_mode"] = True
    agent.on_track_change({"artist": "A", "title": "Quiet"})
    assert agent._pending_track_announce is None
    asyncio.run(agent._tick())
    assert room == []


def test_kill_no_announce(announce_agent):
    from sentinel import set_killed

    agent, settings, clock, spoken, noted = announce_agent
    room = []
    agent.set_publish_halo_hook(lambda name, text: room.append({"name": name, "text": text}))
    set_killed(True)
    try:
        agent.on_track_change({"artist": "A", "title": "Killed"})
        assert agent._pending_track_announce is None
        asyncio.run(agent._tick())
        assert room == []
    finally:
        set_killed(False)


def test_history_endpoint_and_jsonl(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DJ_BACKEND", "sim")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.delenv("OPERATOR_KEY", raising=False)
    monkeypatch.setattr("settings.SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr("settings.APP_DIR", tmp_path)

    from fastapi.testclient import TestClient
    import main as main_mod
    from ops import sqlite_paths

    with TestClient(main_mod.app) as client:
        assert main_mod.nowplaying_svc is not None
        r0 = client.get("/api/nowplaying/history")
        assert r0.status_code == 200
        assert r0.json() == {"tracks": []}

        main_mod.nowplaying_svc.apply_manual_now(
            {"artist": "Charlotte de Witte", "title": "Selected", "bpm": 138}
        )
        main_mod.nowplaying_svc.apply_manual_now(
            {"artist": "Lane 8", "title": "Fingerprint", "bpm": 124}
        )
        r = client.get("/api/nowplaying/history")
        assert r.status_code == 200
        tracks = r.json()["tracks"]
        assert len(tracks) == 2
        assert tracks[-1]["title"] == "Fingerprint"
        assert client.get("/nowplaying/history").json()["tracks"][-1]["title"] == "Fingerprint"

        path = tmp_path / "nowplaying.jsonl"
        assert path.is_file()
        lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
        assert len(lines) >= 2
        assert any(p.name == "nowplaying.jsonl" for p in sqlite_paths())


def test_segment_on_transition_uses_artist():
    from segments import SegmentScheduler

    clock = FakeClock()
    sched = SegmentScheduler(clock=clock, rng=lambda: 0.0)
    ev = sched.on_transition(
        [{"name": "track_id", "enabled": True, "dj_mode_ok": True}],
        title="Hypnotized",
        artist="Amelie Lens",
        bpm=150,
        now=clock(),
    )
    assert ev is not None
    assert "Hypnotized" in ev.line
    assert "Amelie Lens" in ev.line
    assert ev.meta.get("bpm") == 150
