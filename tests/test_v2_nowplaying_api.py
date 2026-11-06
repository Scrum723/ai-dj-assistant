"""V2: Now Playing HTTP + SSE + WS broadcast."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"


@pytest.fixture
def np_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DJ_BACKEND", "sim")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.delenv("OPERATOR_KEY", raising=False)
    monkeypatch.setattr("settings.SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr("settings.APP_DIR", tmp_path)
    from fastapi.testclient import TestClient
    import main as main_mod

    with TestClient(main_mod.app) as client:
        yield client, main_mod


def test_get_nowplaying_empty_and_alias(np_client):
    client, main_mod = np_client
    r = client.get("/api/nowplaying")
    assert r.status_code == 200
    assert r.json() == {} or r.json().get("unavailable")
    r2 = client.get("/nowplaying")
    assert r2.status_code == 200
    assert r2.json() == r.json()


def test_post_and_delete_manual_local(np_client):
    client, main_mod = np_client
    body = {"artist": "Lane 8", "title": "Fingerprint", "bpm": 124}
    r = client.post("/api/nowplaying", json=body)
    assert r.status_code == 200
    got = r.json()
    assert got["artist"] == "Lane 8"
    assert got["title"] == "Fingerprint"
    assert got["source"] == "manual"
    assert client.get("/nowplaying").json()["title"] == "Fingerprint"

    d = client.delete("/api/nowplaying")
    assert d.status_code == 200
    assert d.json() == {}


def test_post_requires_operator_when_key_set(np_client, monkeypatch):
    client, main_mod = np_client
    monkeypatch.setenv("OPERATOR_KEY", "secret-booth-key")
    os.environ["OPERATOR_KEY"] = "secret-booth-key"
    try:
        denied = client.post(
            "/api/nowplaying",
            json={"artist": "X", "title": "Y"},
        )
        assert denied.status_code in (401, 403)

        ok = client.post(
            "/nowplaying",
            json={"artist": "X", "title": "Y"},
            headers={"X-Operator-Key": "secret-booth-key"},
        )
        assert ok.status_code == 200
        assert ok.json()["artist"] == "X"
    finally:
        os.environ.pop("OPERATOR_KEY", None)
        monkeypatch.delenv("OPERATOR_KEY", raising=False)


def test_sse_receives_track_change(np_client):
    client, main_mod = np_client
    assert main_mod.nowplaying_svc is not None
    lines: list[str] = []
    stop = threading.Event()

    def reader():
        with client.stream("GET", "/api/nowplaying/stream") as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in (resp.headers.get("content-type") or "")
            for line in resp.iter_lines():
                if stop.is_set():
                    break
                if line:
                    lines.append(line)
                # wait until we see a non-empty track_change data payload
                if line.startswith("data:") and '"title"' in line:
                    stop.set()
                    break

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    time.sleep(0.25)
    main_mod.nowplaying_svc.apply_manual_now(
        {"artist": "Amelie Lens", "title": "Hypnotized", "bpm": 150}
    )
    t.join(timeout=8)
    stop.set()
    joined = "\n".join(lines)
    assert "event: track_change" in joined or any(x.startswith("data:") for x in lines)
    assert "Hypnotized" in joined


def test_ws_receives_nowplaying_type(np_client):
    client, main_mod = np_client
    assert main_mod.nowplaying_svc is not None

    with client.websocket_connect("/ws") as ws:
        first = ws.receive_json()
        assert first.get("type") == "state"
        main_mod.nowplaying_svc.apply_manual_now(
            {"artist": "Charlotte de Witte", "title": "Selected", "bpm": 138}
        )
        deadline = time.time() + 5
        got = None
        while time.time() < deadline:
            msg = ws.receive_json()
            if msg.get("type") != "nowplaying":
                continue
            payload = msg.get("payload") or {}
            # Accept nested track shapes; ignore unrelated mid-flight broadcasts.
            title = payload.get("title") or (payload.get("track") or {}).get("title")
            artist = payload.get("artist") or (payload.get("track") or {}).get("artist")
            if title == "Selected" and artist == "Charlotte de Witte":
                got = payload
                break
        assert got is not None
        assert got.get("title") == "Selected" or (got.get("track") or {}).get("title") == "Selected"


def test_nowplaying_js_served(np_client):
    client, _ = np_client
    r = client.get("/visualizer/js/nowplaying.js")
    assert r.status_code == 200
    text = r.text
    assert "EventSource" in text
    assert "/api/nowplaying/stream" in text
    html = client.get("/visualizer/").text
    assert "js/nowplaying.js" in html
