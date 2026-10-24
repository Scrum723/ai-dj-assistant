"""C5: Safety fallback — MIDI takeover, silence recover, heartbeat stall."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest


def _midi_msg(typ="control_change", channel=0, control=12, value=64, note=60, velocity=100):
    from sim.deck_sim import MidiMessage

    return MidiMessage(type=typ, channel=channel, control=control, value=value, note=note, velocity=velocity)


@pytest.fixture
def safety_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DJ_BACKEND", "sim")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr("settings.SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr("settings.APP_DIR", tmp_path)
    from sentinel import set_killed
    from settings import save

    set_killed(False)
    save({"dj": {"automation": True, "manual_takeover": False, "takeover_minutes": 5}})
    yield tmp_path
    set_killed(False)


@pytest.fixture
def ctl_mixer_wd(safety_env):
    from dj_controller import DJController
    from mixer import Mixer
    from watchdog import Watchdog

    log_path = safety_env / "dj_actions.jsonl"
    ctl = DJController(backend="sim", actions_log=log_path)
    mixer = Mixer(ctl, enabled=True)
    wd = Watchdog(ctl, mixer, automation_fn=lambda: True)
    mixer.on_heartbeat = wd.tick_heartbeat
    paused = {"n": 0}

    def _on_to():
        mixer.pause()
        paused["n"] += 1

    ctl.set_takeover_callback(_on_to)
    return ctl, mixer, wd, log_path, paused


def test_foreign_cc_triggers_takeover_and_pauses_mixer(ctl_mixer_wd):
    from settings import load

    ctl, mixer, wd, log_path, paused = ctl_mixer_wd
    mixer.state = mixer.state.__class__.PLAYING
    t0 = time.perf_counter()
    r = ctl.ingest_midi_input(_midi_msg(control=12, value=80))
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert r and r.get("manual_takeover") is True
    assert elapsed_ms < 200
    assert load()["dj"]["manual_takeover"] is True
    assert mixer._paused is True
    assert paused["n"] == 1
    # auto sends refused while takeover
    refused = ctl.send("eq_low", deck=1, value=0.2, source="auto")
    assert refused["ok"] is False
    assert refused["reason"] == "manual_takeover"


def test_foreign_note_triggers_takeover(ctl_mixer_wd):
    ctl, mixer, wd, log_path, paused = ctl_mixer_wd
    r = ctl.ingest_midi_input(_midi_msg(typ="note_on", note=48, velocity=100))
    assert r and r.get("ok")
    assert paused["n"] == 1


def test_send_logs_dj_actions_jsonl(ctl_mixer_wd):
    ctl, mixer, wd, log_path, paused = ctl_mixer_wd
    ctl.send("crossfader", value=0.5, source="manual")
    assert log_path.exists()
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1
    assert "crossfader" in lines[-1]


def test_silence_triggers_backup_deck(ctl_mixer_wd):
    ctl, mixer, wd, log_path, paused = ctl_mixer_wd
    sim = ctl.simulator
    sim.load_track(1, {"id": 1, "bpm": 128})
    sim.load_track(2, {"id": 2, "bpm": 128})
    # Deck 1 was playing then went silent
    d1 = sim.deck(1)
    d1.playing = False
    d1.volume = 0.0
    sim.deck(2).playing = False
    mixer.active_deck = 1
    mixer.enabled = True
    # Force silence age past threshold
    wd.last_audible_at = wd.clock() - 4.0
    wd._silence_armed = True
    assert sim.output_level() < 0.02
    out = wd.check()
    assert "silence_recover" in out["actions"]
    assert wd.silence_recovered is True
    assert sim.deck(2).playing is True
    assert sim.crossfader >= 0.9
    assert any(a["kind"] == "silence" for a in wd.alerts)


def test_heartbeat_stall_triggers_panic(ctl_mixer_wd):
    from mixer import MixState

    ctl, mixer, wd, log_path, paused = ctl_mixer_wd
    sim = ctl.simulator
    sim.load_track(1, {"id": 1, "bpm": 128})
    d1 = sim.deck(1)
    d1.playing = True
    d1.eq_low = 0.1
    mixer.enabled = True
    mixer._paused = False
    mixer.state = MixState.PLAYING
    wd.last_heartbeat = wd.clock() - 3.0
    out = wd.check()
    assert "stall_panic" in out["actions"]
    assert wd.panic_triggered is True
    assert abs(d1.eq_low - 0.5) < 1e-6
    assert any(a["kind"] == "stall" for a in wd.alerts)


def test_watchdog_status_lists_charles_blockers(ctl_mixer_wd):
    ctl, mixer, wd, log_path, paused = ctl_mixer_wd
    st = wd.status()
    blockers = " ".join(st["charles_blockers"]).lower()
    assert "controller" in blockers
    assert "blackhole" in blockers
    assert "rekordbox" in blockers


@pytest.mark.skipif(
    sys.platform != "darwin" or os.getenv("CI") == "true",
    reason="Mac-only sounddevice/BlackHole loopback; skipped in CI",
)
def test_sounddevice_level_mac_only():
    from watchdog import sounddevice_level

    # May return 0 without BlackHole; just ensures guarded import path runs
    level = sounddevice_level()
    assert isinstance(level, float)
    assert 0.0 <= level <= 1.0


@pytest.mark.skipif(
    sys.platform != "darwin" or os.getenv("CI") == "true",
    reason="Mac-only real MIDI input port; skipped in CI",
)
def test_midi_input_start_mac_only(safety_env):
    from dj_controller import DJController

    ctl = DJController(backend="sim", actions_log=safety_env / "dj_actions.jsonl")
    # Without a configured hardware port this returns no_port / open error — must not crash
    r = ctl.start_midi_input("")
    assert r.get("ok") is False
    ctl.stop_midi_input()
