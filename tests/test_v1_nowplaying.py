"""V1: Now Playing service — sources, norm, dedupe/debounce (fake clock)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "nowplaying"


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.t = float(start)

    def __call__(self) -> float:
        return self.t

    def advance(self, sec: float) -> None:
        self.t += float(sec)


@pytest.fixture
def clock():
    return FakeClock()


def _svc(**kwargs):
    from nowplaying import NowPlaying

    return NowPlaying(**kwargs)


def test_norm_wnp_fixture():
    from nowplaying import norm

    raw = json.loads((FIXTURES / "wnp_last.json").read_text(encoding="utf-8"))
    n = norm(raw, source="wnp")
    assert n is not None
    assert n["artist"] == "Above & Beyond"
    assert n["title"] == "Sun & Moon"
    assert n["bpm"] == 132.0
    assert n["key"] == "A min"
    assert n["art"] == "cover.png"
    assert n["source"] == "wnp"


def test_norm_file_text_and_json():
    from nowplaying import norm

    text = (FIXTURES / "file_track.txt").read_text(encoding="utf-8")
    n = norm(text, source="file")
    assert n["artist"] == "Lane 8"
    assert n["title"] == "Little Helper 291"

    raw = json.loads((FIXTURES / "file_track.json").read_text(encoding="utf-8"))
    n2 = norm(raw, source="file")
    assert n2["artist"] == "Amelie Lens"
    assert n2["title"] == "Hypnotized"
    assert n2["bpm"] == 150.0
    assert n2["art"] == "https://example.com/art.jpg"


def test_norm_track_envelope():
    from nowplaying import norm

    raw = json.loads((FIXTURES / "bridge_nowplaying.json").read_text(encoding="utf-8"))
    n = norm(raw)
    assert n["artist"] == "Charlotte de Witte"
    assert n["title"] == "Selected"
    assert n["source"] == "bridge"
    assert n["bpm"] == 138.0


def test_read_wnp_from_fixture(clock):
    payload = json.loads((FIXTURES / "wnp_last.json").read_text(encoding="utf-8"))

    def fake_http(url: str):
        assert "8899" in url or url.endswith("/v1/last")
        return payload

    svc = _svc(sources=["wnp"], clock=clock, http_get_json=fake_http)
    got = svc.read_wnp()
    assert not got.get("unavailable")
    assert got["artist"] == "Above & Beyond"
    assert got["title"] == "Sun & Moon"
    assert got["source"] == "wnp"


def test_read_file_text_and_json(clock, tmp_path):
    text_path = tmp_path / "np.txt"
    text_path.write_text((FIXTURES / "file_track.txt").read_text(encoding="utf-8"), encoding="utf-8")
    svc = _svc(sources=["file"], file_path=str(text_path), clock=clock)
    got = svc.read_file()
    assert got["artist"] == "Lane 8"
    assert got["title"] == "Little Helper 291"

    json_path = tmp_path / "np.json"
    json_path.write_text((FIXTURES / "file_track.json").read_text(encoding="utf-8"), encoding="utf-8")
    svc2 = _svc(sources=["file"], file_path=str(json_path), clock=clock)
    got2 = svc2.read_file()
    assert got2["artist"] == "Amelie Lens"
    assert got2["bpm"] == 150.0


def test_read_bridge(clock):
    payload = json.loads((FIXTURES / "bridge_nowplaying.json").read_text(encoding="utf-8"))

    def fake_http(url: str):
        return payload

    svc = _svc(
        sources=["bridge"],
        bridge_url="http://127.0.0.1:8787/nowplaying",
        clock=clock,
        http_get_json=fake_http,
    )
    got = svc.read_bridge()
    assert got["artist"] == "Charlotte de Witte"
    assert got["source"] == "bridge"


def test_internal_enriches_from_library(clock, tmp_path):
    db = tmp_path / "library.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE tracks (id INTEGER PRIMARY KEY, filepath TEXT, filename TEXT, bpm REAL, key TEXT, energy REAL)"
        )
        conn.execute(
            "INSERT INTO tracks (id, filepath, filename, bpm, key, energy) VALUES (1, '/x/a.mp3', 'Artist X - Song Y.mp3', 128.5, '8A', 0.7)"
        )

    def get_track():
        return {"id": 1, "filename": "Artist X - Song Y.mp3"}

    svc = _svc(sources=["internal"], get_internal_track=get_track, library_db=str(db), clock=clock)
    got = svc.read_internal()
    assert got["artist"] == "Artist X"
    assert got["title"] == "Song Y"
    assert got["bpm"] == 128.5
    assert got["key"] == "8A"
    assert got["track_id"] == 1
    assert got["source"] == "internal"


def test_manual_source(clock):
    svc = _svc(sources=["manual"], clock=clock)
    assert svc.read_manual().get("unavailable")
    svc.set_manual({"artist": "Manual", "title": "Entry", "bpm": 120})
    got = svc.read_manual()
    assert got["artist"] == "Manual"
    assert got["source"] == "manual"


def test_source_order_prefers_first_available(clock):
    calls = []

    def fake_http(url: str):
        calls.append(url)
        return {"artist": "WNP", "title": "Track"}

    svc = _svc(
        sources=["internal", "wnp"],
        get_internal_track=lambda: None,
        clock=clock,
        http_get_json=fake_http,
    )
    got = svc.poll_once()
    assert got["artist"] == "WNP"
    assert got["source"] == "wnp"
    assert calls


def test_cloud_marks_network_sources_unavailable(clock, monkeypatch, tmp_path):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    f = tmp_path / "np.txt"
    f.write_text("A - B", encoding="utf-8")
    svc = _svc(
        sources=["wnp", "file", "bridge"],
        file_path=str(f),
        bridge_url="http://127.0.0.1:9/nowplaying",
        clock=clock,
        http_get_json=lambda u: (_ for _ in ()).throw(AssertionError("no http on cloud")),
    )
    assert svc.read_wnp().get("unavailable")
    assert svc.read_file().get("unavailable")
    assert svc.read_bridge().get("unavailable")


def test_dedupe_same_artist_title(clock):
    events = []
    svc = _svc(sources=["manual"], clock=clock, debounce_sec=3.0)
    svc.on_track_change(lambda t: events.append(t))
    a = {"artist": "A", "title": "One", "bpm": 120, "source": "manual"}
    # establish current after debounce
    assert svc.apply_candidate(a, now=clock()) is False
    clock.advance(3.0)
    assert svc.apply_candidate(a, now=clock()) is True
    assert len(events) == 1
    # same key — no new event
    assert svc.apply_candidate({**a, "bpm": 121}, now=clock()) is False
    assert len(events) == 1
    assert svc.current()["bpm"] == 121.0


def test_debounce_cue_preview_flicker(clock):
    events = []
    svc = _svc(sources=["manual"], clock=clock, debounce_sec=3.0)
    svc.on_track_change(lambda t: events.append(dict(t)))

    t1 = {"artist": "A", "title": "One", "source": "x"}
    t2 = {"artist": "B", "title": "Two", "source": "x"}

    svc.apply_candidate(t1, now=clock())
    clock.advance(3.0)
    assert svc.apply_candidate(t1, now=clock()) is True
    assert events[-1]["title"] == "One"

    # flicker to t2 then back to t1 within debounce — no commit of t2
    clock.advance(0.5)
    assert svc.apply_candidate(t2, now=clock()) is False
    clock.advance(1.0)
    assert svc.apply_candidate(t1, now=clock()) is False  # back to current key
    assert len(events) == 1

    # stable new track for full debounce → emit
    clock.advance(0.5)
    assert svc.apply_candidate(t2, now=clock()) is False
    clock.advance(3.0)
    assert svc.apply_candidate(t2, now=clock()) is True
    assert events[-1]["title"] == "Two"
    assert len(events) == 2


def test_tick_never_raises_on_bad_http(clock):
    def boom(url: str):
        raise ConnectionError("down")

    svc = _svc(sources=["wnp"], clock=clock, http_get_json=boom, debounce_sec=0)
    assert svc.tick(now=clock()) is False
    assert svc.state() == {}


def test_from_settings_env_overrides(monkeypatch, clock):
    monkeypatch.setenv("NOWPLAYING_SOURCES", "manual,file")
    monkeypatch.setenv("NOWPLAYING_POLL_SEC", "5")
    monkeypatch.setenv("NOWPLAYING_WNP_URL", "http://example:8899/v1/last")
    from nowplaying import NowPlaying

    svc = NowPlaying.from_settings({"nowplaying": {"sources": ["internal"]}}, clock=clock)
    assert svc.sources == ["manual", "file"]
    assert svc.poll_sec == 5.0
    assert svc.wnp_url.endswith("/v1/last")


def test_settings_defaults_include_nowplaying():
    from settings import DEFAULTS

    np = DEFAULTS["nowplaying"]
    assert np["sources"] == ["internal", "wnp", "file"]
    assert np["poll_sec"] == 2.0
    assert "8899" in np["wnp_url"]


def test_lifespan_starts_nowplaying(tmp_path, monkeypatch):
    """Cloud-safe boot: TestClient lifespan creates the poll task."""
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.setenv("DJ_BACKEND", "sim")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr("settings.SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr("settings.APP_DIR", tmp_path)
    from fastapi.testclient import TestClient
    import main as main_mod

    with TestClient(main_mod.app) as client:
        r = client.get("/healthz")
        assert r.status_code == 200
        assert main_mod.nowplaying_svc is not None
        assert main_mod.nowplaying_svc.read_wnp().get("unavailable")
