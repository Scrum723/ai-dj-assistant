"""C4: Mix engine, replays, transition scoring — fake clock, under 5s."""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient


class FakeClock:
    def __init__(self, start: float = 0.0):
        self.t = float(start)

    def __call__(self) -> float:
        return self.t

    def advance(self, sec: float) -> None:
        self.t += float(sec)


def _tracks(n: int = 10) -> list[dict]:
    from mixer import bars_to_sec

    out = []
    for i in range(n):
        bpm = 128.0
        dur = bars_to_sec(96, bpm)
        outro = bars_to_sec(64, bpm)
        phrases = [
            {"start": bars_to_sec(b, bpm), "end": bars_to_sec(min(b + 16, 96), bpm), "bars": 16}
            for b in range(0, 97, 16)
        ]
        out.append(
            {
                "id": i + 1,
                "filename": f"T{i + 1:02d}.mp3",
                "bpm": bpm,
                "camelot": f"{(i % 12) + 1}A",
                "energy": round(0.4 + i * 0.04, 3),
                "duration_sec": dur,
                "outro_sec": outro,
                "phrases": phrases,
            }
        )
    return out


@pytest.fixture
def automation_on(tmp_path, monkeypatch):
    monkeypatch.setenv("DJ_BACKEND", "sim")
    monkeypatch.setattr("settings.SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr("settings.APP_DIR", tmp_path)
    from sentinel import set_killed
    from settings import save

    set_killed(False)
    save({"dj": {"automation": True, "manual_takeover": False}})
    yield
    set_killed(False)
    save({"dj": {"automation": False, "manual_takeover": False}})


def test_automation_default_off_in_settings():
    from settings import DEFAULTS

    assert DEFAULTS["dj"]["automation"] is False


def test_transition_start_on_phrase():
    from mixer import transition_start_beat

    tracks = _tracks(1)
    beat = transition_start_beat(tracks[0], lead_bars=32)
    # outro at 64 bars → target 32 bars = 128 beats; phrase grid every 64 beats
    assert beat == pytest.approx(128.0, abs=0.01)


def test_templates_exist():
    from mixer import TEMPLATES

    assert set(TEMPLATES) >= {"bass_swap_blend", "filter_out", "phrase_cut"}
    for steps in TEMPLATES.values():
        assert steps
        assert all(hasattr(s, "at_bar") for s in steps)


def test_fake_clock_10_track_set_no_silence(automation_on):
    from dj_controller import DJController
    from mixer import Mixer
    from sim.score import score_set

    clock = FakeClock()

    async def fake_sleep(dt: float) -> None:
        clock.advance(max(0.0, float(dt)))

    async def _run():
        ctl = DJController(backend="sim")
        ctl.simulator.clock = clock
        mix = Mixer(ctl, clock=clock, sleep=fake_sleep, enabled=True)
        tracks = _tracks(10)
        t0 = time.perf_counter()
        transitions = await mix.run_set(tracks, template="bass_swap_blend", play_lead_bars=1.0)
        wall = time.perf_counter() - t0
        return mix, tracks, transitions, wall

    mix, tracks, transitions, wall = asyncio.run(_run())
    assert wall < 5.0, f"set took {wall:.2f}s"
    assert len(transitions) == 9
    assert all(tr.silence_sec == 0.0 for tr in transitions)
    for tr in transitions:
        assert abs(tr.start_beat - tr.phrase_beat) <= 1.0 + 1e-6
    report = score_set(tracks, transitions)
    assert report["count"] == 9
    assert report["all_phrase_aligned"] is True
    assert report["silence_sec"] == 0.0
    assert report["average"] >= 70.0
    assert any(e.get("source") == "auto" for e in mix._log)


def test_scorer_cases():
    from sim.score import grade_from_score, score_transition

    a = {"id": 1, "bpm": 128, "camelot": "8A", "energy": 0.5}
    b = {"id": 2, "bpm": 128, "camelot": "8A", "energy": 0.52}
    good = score_transition(
        a, b, beat_offset_ms=0.0, template="phrase_cut", bass_clash_sec=0.0, loudness_overlap=0.3
    )
    assert good.phrase_aligned is True
    assert good.camelot_ok is True
    assert good.score >= 85
    assert good.grade in ("A", "B")

    bad = score_transition(
        a,
        {"id": 3, "bpm": 150, "camelot": "3B", "energy": 0.95},
        beat_offset_ms=2000.0,
        template="filter_out",
        bass_clash_sec=5.0,
        loudness_overlap=0.95,
    )
    assert bad.phrase_aligned is False
    assert bad.score < good.score
    assert grade_from_score(95) == "A"
    assert grade_from_score(50) == "F"


def test_replay_parser_json(tmp_path):
    from sim.replay import load_replay, parse_json_replay

    data = [
        {"track_id": 1, "start_sec": 0},
        {"track_id": 2, "start_sec": 180.5},
        {"track_id": "x", "start_sec": "3:05.0", "filename": "X.mp3"},
    ]
    parsed = parse_json_replay(data)
    assert parsed[0]["track_id"] == 1
    assert parsed[1]["start_sec"] == pytest.approx(180.5)
    assert parsed[2]["start_sec"] == pytest.approx(185.0)

    path = tmp_path / "set.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert len(load_replay(path)) == 3


def test_replay_parser_csv(tmp_path):
    from sim.replay import load_replay

    csv_text = "Track Title,Artist,Start Time,BPM\nOne,A,0:00,128\nTwo,B,3:00,130\n"
    path = tmp_path / "hist.csv"
    path.write_text(csv_text, encoding="utf-8")
    entries = load_replay(path)
    assert len(entries) == 2
    assert entries[1]["start_sec"] == pytest.approx(180.0)


def test_post_dj_automation_operator(tmp_path, monkeypatch):
    monkeypatch.setenv("DJ_BACKEND", "sim")
    monkeypatch.setenv("OPERATOR_KEY", "test-op-key")
    monkeypatch.setattr("settings.SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr("settings.APP_DIR", tmp_path)
    from settings import save

    save({"dj": {"automation": False, "manual_takeover": False}})

    import main as main_mod

    main_mod.mixer.enabled = False
    client = TestClient(main_mod.app)
    # Default off in snapshot
    st = client.get("/state").json()
    assert st.get("automation") is False

    r = client.post(
        "/dj/automation",
        json={"enabled": True},
        headers={"X-Operator-Key": "test-op-key"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["automation"] is True
    assert main_mod.mixer.enabled is True

    r2 = client.post(
        "/dj/automation",
        json={"enabled": False},
        headers={"X-Operator-Key": "test-op-key"},
    )
    assert r2.json()["automation"] is False
    assert main_mod.mixer.enabled is False


def test_auto_send_gated_when_automation_off(tmp_path, monkeypatch):
    monkeypatch.setenv("DJ_BACKEND", "sim")
    monkeypatch.setattr("settings.SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr("settings.APP_DIR", tmp_path)
    from dj_controller import DJController
    from mixer import Mixer
    from settings import save

    save({"dj": {"automation": False, "manual_takeover": False}})
    clock = FakeClock()

    async def fake_sleep(dt: float) -> None:
        clock.advance(dt)

    ctl = DJController(backend="sim")
    mix = Mixer(ctl, clock=clock, sleep=fake_sleep)
    r = mix._send("crossfader", value=0.5)
    assert r["ok"] is False
    assert r["reason"] == "automation_off"


def test_phrase_cut_and_filter_templates(automation_on):
    from dj_controller import DJController
    from mixer import Mixer

    clock = FakeClock()

    async def fake_sleep(dt: float) -> None:
        clock.advance(dt)

    async def _run():
        ctl = DJController(backend="sim")
        ctl.simulator.clock = clock
        mix = Mixer(ctl, clock=clock, sleep=fake_sleep, enabled=True)
        tracks = _tracks(3)
        tr = await mix.run_set(tracks, template="phrase_cut", play_lead_bars=0.5)
        mix2 = Mixer(ctl, clock=clock, sleep=fake_sleep, enabled=True)
        tr2 = await mix2.run_set(tracks, template="filter_out", play_lead_bars=0.5)
        return tr, tr2

    tr, tr2 = asyncio.run(_run())
    assert len(tr) == 2
    assert tr[0].template == "phrase_cut"
    assert tr2[0].template == "filter_out"
