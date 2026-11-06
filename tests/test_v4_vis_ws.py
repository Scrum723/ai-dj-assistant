"""V4: Visualizer remote control websocket + /api/visualizer."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def vis_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DJ_BACKEND", "sim")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.delenv("OPERATOR_KEY", raising=False)
    monkeypatch.setattr("settings.SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr("settings.APP_DIR", tmp_path)
    from fastapi.testclient import TestClient
    import main as main_mod
    from visualizer_ctl import DEFAULT_STATE, vis_ctl

    # Reset singleton between tests
    vis_ctl.state = dict(DEFAULT_STATE)
    vis_ctl.clients.clear()
    vis_ctl.last_status = None

    with TestClient(main_mod.app) as client:
        yield client, main_mod, vis_ctl


def test_get_visualizer_state(vis_client):
    client, _, vis_ctl = vis_client
    r = client.get("/api/visualizer")
    assert r.status_code == 200
    body = r.json()
    assert body["clients"] == 0
    assert body["state"]["strobeSafe"] is True
    assert body["state"]["mode"] == "story"
    assert "songSync" in body["state"]


def test_post_requires_operator_when_key_set(vis_client, monkeypatch):
    client, _, _ = vis_client
    monkeypatch.setenv("OPERATOR_KEY", "secret-booth-key")
    os.environ["OPERATOR_KEY"] = "secret-booth-key"
    try:
        denied = client.post("/api/visualizer", json={"mode": "loop"})
        assert denied.status_code in (401, 403)

        ok = client.post(
            "/api/visualizer",
            json={"mode": "loop"},
            headers={"X-Operator-Key": "secret-booth-key"},
        )
        assert ok.status_code == 200
        assert ok.json()["state"]["mode"] == "loop"
    finally:
        os.environ.pop("OPERATOR_KEY", None)
        monkeypatch.delenv("OPERATOR_KEY", raising=False)


def test_invalid_command_422(vis_client):
    client, _, _ = vis_client
    r = client.post("/api/visualizer", json={"mode": "nope"})
    assert r.status_code == 422
    r2 = client.post("/api/visualizer", json={"reactivity": 99})
    assert r2.status_code == 422
    r3 = client.post("/api/visualizer", json={"strobeSafe": False})
    assert r3.status_code == 422
    r4 = client.post("/api/visualizer", json={"unknownField": 1})
    assert r4.status_code == 422


def test_strobe_safe_off_with_confirm(vis_client):
    client, _, vis_ctl = vis_client
    r = client.post("/api/visualizer", json={"strobeSafe": False, "confirm": True})
    assert r.status_code == 200
    assert vis_ctl.state["strobeSafe"] is False


def test_ws_receives_cmd_after_post(vis_client):
    client, _, _ = vis_client
    with client.websocket_connect("/ws/visualizer") as ws:
        first = ws.receive_json()
        assert first.get("type") == "vis_cmd"
        assert "mode" in first

        r = client.post("/api/visualizer", json={"mode": "shuffle", "reactivity": 2.0})
        assert r.status_code == 200

        deadline = time.time() + 5
        got = None
        while time.time() < deadline:
            msg = ws.receive_json()
            if msg.get("type") == "vis_cmd" and msg.get("mode") == "shuffle":
                got = msg
                break
        assert got is not None
        assert got["reactivity"] == 2.0


def test_state_persists_across_reconnect(vis_client):
    client, _, vis_ctl = vis_client
    r = client.post("/api/visualizer", json={"scene": 3, "songSync": True, "ui": False})
    assert r.status_code == 200
    assert vis_ctl.state["scene"] == 3
    assert vis_ctl.state["songSync"] is True

    with client.websocket_connect("/ws/visualizer") as ws:
        snap = ws.receive_json()
        assert snap["type"] == "vis_cmd"
        assert snap["scene"] == 3
        assert snap["songSync"] is True
        assert snap["ui"] is False


def test_vis_status_from_client(vis_client):
    client, _, vis_ctl = vis_client
    with client.websocket_connect("/ws/visualizer") as ws:
        ws.receive_json()  # initial state
        ws.send_json(
            {
                "type": "vis_status",
                "scene": 2,
                "mode": "story",
                "fps": 60,
                "input": "BlackHole 2ch",
                "audio": "SHOULD_NOT_STORE",
            }
        )
        # allow server to process
        time.sleep(0.15)
    g = client.get("/api/visualizer").json()
    assert g["status"] is not None
    assert g["status"]["fps"] == 60
    assert g["status"]["input"] == "BlackHole 2ch"
    assert "audio" not in g["status"]


def test_song_sync_broadcast_on_track_change(vis_client):
    client, main_mod, vis_ctl = vis_client
    client.post("/api/visualizer", json={"songSync": True})
    with client.websocket_connect("/ws/visualizer") as ws:
        ws.receive_json()
        assert main_mod.nowplaying_svc is not None
        main_mod.nowplaying_svc.apply_manual_now(
            {"artist": "Amelie Lens", "title": "Hypnotized", "bpm": 150, "duration": 240}
        )
        deadline = time.time() + 5
        got = None
        while time.time() < deadline:
            msg = ws.receive_json()
            if msg.get("type") == "vis_cmd" and msg.get("next"):
                got = msg
                break
        assert got is not None
        assert got.get("resetAct") is True
        assert got.get("sceneMinutes") == 4.0  # 240s → 4 min


def test_remote_js_served(vis_client):
    client, _, _ = vis_client
    r = client.get("/visualizer/js/remote.js")
    assert r.status_code == 200
    assert "/ws/visualizer" in r.text
    assert "vis_status" in r.text
    html = client.get("/visualizer/").text
    assert "js/remote.js" in html
