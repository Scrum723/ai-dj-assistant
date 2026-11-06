"""V7: /healthz nowplaying+visualizer fields; /metrics announce + track_id counts."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"


@pytest.fixture
def v7_client(tmp_path, monkeypatch):
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


def test_healthz_nowplaying_and_visualizer(v7_client):
    client, main_mod = v7_client
    r = client.get("/healthz")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    np = data["nowplaying"]
    assert set(np) >= {"source", "ok", "last_change_age"}
    assert np["ok"] is True
    assert np["source"] is None
    assert np["last_change_age"] is None
    vis = data["visualizer"]
    assert vis["served"] is True
    assert isinstance(vis["clients"], int)
    assert vis["clients"] == 0


def test_healthz_nowplaying_after_manual_track(v7_client):
    client, main_mod = v7_client
    assert main_mod.nowplaying_svc is not None
    main_mod.nowplaying_svc.apply_manual_now(
        {"artist": "Lane 8", "title": "Fingerprint", "bpm": 124}
    )
    r = client.get("/healthz")
    assert r.status_code == 200
    np = r.json()["nowplaying"]
    assert np["ok"] is True
    assert np["source"] == "manual"
    assert isinstance(np["last_change_age"], (int, float))
    assert np["last_change_age"] >= 0


def test_healthz_visualizer_client_count(v7_client):
    client, main_mod = v7_client
    with client.websocket_connect("/ws/visualizer"):
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json()["visualizer"]["clients"] >= 1


def test_metrics_announce_and_track_id(v7_client):
    client, main_mod = v7_client
    r = client.get("/metrics")
    assert r.status_code == 200
    body = r.json()
    for key in (
        "calls_today",
        "p50_ms",
        "p95_ms",
        "est_cost_usd",
        "announce_count",
        "track_id_answers",
    ):
        assert key in body
    assert body["announce_count"] == 0
    assert body["track_id_answers"] == 0
    if main_mod.agent is not None:
        main_mod.agent._announce_count = 3
        main_mod.agent._track_id_answers = 5
        body2 = client.get("/metrics").json()
        assert body2["announce_count"] == 3
        assert body2["track_id_answers"] == 5
