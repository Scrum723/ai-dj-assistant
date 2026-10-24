"""C1: MIDI control layer, gates, FakeMidiOut / DeckSimulator, endpoints."""

from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def sim_controller(tmp_path, monkeypatch):
    monkeypatch.setenv("DJ_BACKEND", "sim")
    # Isolate settings so automation/takeover flags do not leak
    monkeypatch.setattr(
        "settings.SETTINGS_PATH",
        tmp_path / "settings.json",
    )
    monkeypatch.setattr("settings.APP_DIR", tmp_path)
    from dj_controller import DJController
    from sentinel import set_killed
    from settings import save

    set_killed(False)
    save({"dj": {"automation": False, "manual_takeover": False}})
    ctl = DJController(backend="sim")
    yield ctl
    set_killed(False)


def test_no_mido_at_module_level():
    import dj_controller as dc
    import inspect

    src = inspect.getsource(dc)
    # Top-level import of mido/rtmidi must not appear (lazy _load_mido only)
    assert "\nimport mido" not in src
    assert "\nimport rtmidi" not in src
    assert "from mido" not in src.split("def _load_mido")[0]


def test_midi_map_per_deck_channels():
    from dj_controller import MIDI_MAP

    assert MIDI_MAP["decks"][1]["channel"] == 0
    assert MIDI_MAP["decks"][2]["channel"] == 1
    for deck in (1, 2):
        m = MIDI_MAP["decks"][deck]
        for key in (
            "play",
            "cue",
            "sync",
            "load",
            "volume",
            "filter",
            "eq_low",
            "eq_mid",
            "eq_hi",
            "browse_up",
            "browse_down",
        ):
            assert key in m
    assert MIDI_MAP["crossfader"]["control"] == 12


def test_message_bytes(sim_controller):
    r = sim_controller.send("eq_low", deck=1, value=1.0, source="manual")
    assert r["ok"] is True
    msg = sim_controller.outport.sent[-1]
    assert msg.bytes() == bytes([0xB0, 20, 127])

    sim_controller.send("filter", deck=2, value=0.0, source="manual")
    msg = sim_controller.outport.sent[-1]
    assert msg.bytes() == bytes([0xB1, 16, 0])


def test_gate_killed(sim_controller):
    from sentinel import set_killed

    set_killed(True)
    r = sim_controller.send("play", deck=1, source="manual")
    assert r == {"ok": False, "reason": "killed"}
    set_killed(False)


def test_gate_auto_automation_off(sim_controller, tmp_path, monkeypatch):
    from settings import save

    save({"dj": {"automation": False, "manual_takeover": False}})
    r = sim_controller.send("play", deck=1, source="auto")
    assert r["ok"] is False
    assert r["reason"] == "automation_off"


def test_gate_auto_manual_takeover(sim_controller):
    from settings import save

    save({"dj": {"automation": True, "manual_takeover": True}})
    r = sim_controller.send("crossfader", value=0.5, source="auto")
    assert r["ok"] is False
    assert r["reason"] == "manual_takeover"

    # Manual still allowed
    r2 = sim_controller.send("crossfader", value=0.25, source="manual")
    assert r2["ok"] is True


def test_gate_auto_allowed_when_automation_on(sim_controller):
    from settings import save

    save({"dj": {"automation": True, "manual_takeover": False}})
    r = sim_controller.send("volume", deck=1, value=0.8, source="auto")
    assert r["ok"] is True


def test_play_tap_nonblocking(sim_controller):
    t0 = time.perf_counter()
    r = sim_controller.deck_play_pause(deck=1)
    elapsed = time.perf_counter() - t0
    assert r["ok"] is True
    assert elapsed < 0.05  # must not sleep 80ms on the caller
    time.sleep(0.12)
    # 127 then 0
    plays = [m for m in sim_controller.outport.sent if m.control == 10]
    assert len(plays) >= 2
    assert plays[0].value == 127
    assert plays[-1].value == 0


def test_simulator_state_and_panic(sim_controller):
    sim = sim_controller.simulator
    sim.load_track(1, {"filename": "A.mp3", "bpm": 128})
    sim_controller.send("play", deck=1, source="manual")
    time.sleep(0.12)
    assert sim.deck(1).playing is True

    sim_controller.set_eq(deck=1, band="low", value=0.0)
    sim_controller.set_filter(deck=1, value=0.1)
    sim_controller.crossfader(0.8)
    assert sim.deck(1).eq_low == pytest.approx(0.0, abs=0.02)
    assert sim.crossfader == pytest.approx(0.8, abs=0.02)

    r = sim_controller.panic()
    assert r["ok"] is True
    assert sim.deck(1).eq_low == pytest.approx(0.5, abs=0.02)
    assert sim.deck(1).filter == pytest.approx(0.5, abs=0.02)
    # Playing deck 1 → fader to A
    assert sim.crossfader == pytest.approx(0.0, abs=0.02)
    assert r["playing_deck"] == 1


def test_simulator_position_injectable_clock():
    from sim.deck_sim import DeckSimulator, FakeMidiOut
    from dj_controller import DJController

    t = {"now": 1000.0}

    def clock():
        return t["now"]

    sim = DeckSimulator(clock=clock)
    ctl = DJController(backend="sim", outport=FakeMidiOut(simulator=sim), simulator=sim)
    sim.load_track(1, {"bpm": 120})
    ctl.send("play", deck=1)
    time.sleep(0.1)  # allow tap
    t["now"] = 1002.0  # 2 seconds → 4 beats at 120 BPM
    assert sim.position(1) == pytest.approx(4.0, abs=0.15)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DJ_BACKEND", "sim")
    monkeypatch.setattr("settings.SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr("settings.APP_DIR", tmp_path)
    # Re-bind global dj to sim if main already imported with empty outport
    import main
    from dj_controller import DJController
    from sentinel import set_killed
    from settings import save

    set_killed(False)
    save({"dj": {"automation": False, "manual_takeover": False}})
    main.dj = DJController(backend="sim")
    os.environ.pop("OPERATOR_KEY", None)
    with TestClient(main.app) as c:
        yield c
    set_killed(False)


def test_endpoint_eq_bad_band_422(client):
    r = client.post("/action/eq", json={"deck": 1, "band": "bass", "value": 0.5})
    assert r.status_code == 422


def test_endpoint_bad_deck_422(client):
    r = client.post("/action/play", json={"deck": 3})
    assert r.status_code == 422


def test_endpoint_filter_sync_panic_takeover(client):
    assert client.post("/action/filter", json={"deck": 1, "value": 0.2}).status_code == 200
    assert client.post("/action/sync", json={"deck": 2}).status_code == 200
    panic = client.post("/action/panic")
    assert panic.status_code == 200
    assert panic.json()["status"] == "ok"
    to = client.post("/action/takeover", json={"enabled": True})
    assert to.status_code == 200
    assert to.json()["manual_takeover"] is True

    # Auto refused while takeover on — exercise controller via play source=auto
    import main

    main.dj  # noqa: B018
    from settings import save

    save({"dj": {"automation": True, "manual_takeover": True}})
    refused = main.dj.send("play", deck=1, source="auto")
    assert refused["reason"] == "manual_takeover"


def test_endpoint_eq_ok(client):
    r = client.post("/action/eq", json={"deck": 2, "band": "mid", "value": 0.75})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["band"] == "mid"
