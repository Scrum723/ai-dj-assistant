"""C3: Next-track selection — deterministic scoring on a 30-track fixture."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from selector import (
    PLANS,
    bpm_ratio_score,
    camelot_fit_score,
    pick,
    plan_names,
    score,
)


def _fixture_tracks(n: int = 30) -> list[dict]:
    """30 tracks spanning BPM, Camelot, energy for deterministic picks."""
    # Alternating Camelot around the wheel; energy ramps; BPM cluster ~128 + outliers
    camelots = [f"{(i % 12) + 1}{'A' if i % 2 == 0 else 'B'}" for i in range(n)]
    tracks = []
    for i in range(n):
        bpm = 128.0
        if i == 10:
            bpm = 64.0  # half-time of 128
        elif i == 11:
            bpm = 256.0  # double
        elif i == 12:
            bpm = 140.0
        elif i >= 20:
            bpm = 122.0 + (i % 5)
        energy = round(0.15 + (i / (n - 1)) * 0.75, 3)
        tracks.append(
            {
                "id": i + 1,
                "filename": f"Track_{i + 1:02d}.mp3",
                "bpm": bpm,
                "key": "Am",
                "camelot": camelots[i],
                "energy": energy,
            }
        )
    return tracks


@pytest.fixture
def library():
    return _fixture_tracks(30)


def test_plans_warmup_build_peak_cooldown():
    assert set(plan_names()) == {"warmup", "build", "peak", "cooldown"}
    for name in plan_names():
        assert "target" in PLANS[name]
        assert "step" in PLANS[name]


def test_bpm_half_double():
    assert bpm_ratio_score(128, 128) == pytest.approx(1.0, abs=0.01)
    assert bpm_ratio_score(128, 64) == pytest.approx(1.0, abs=0.01)
    assert bpm_ratio_score(128, 256) == pytest.approx(1.0, abs=0.01)
    assert bpm_ratio_score(128, 140) < 0.85


def test_camelot_neighbours():
    assert camelot_fit_score("8A", "8A") == 1.0
    assert camelot_fit_score("8A", "9A") == pytest.approx(0.9)
    assert camelot_fit_score("8A", "8B") == pytest.approx(0.75)
    assert camelot_fit_score("8A", "3B") < 0.5


def test_no_repeats_within_last_n(library):
    current = library[0]  # id=1, 8A-ish camelot[0]=1A
    recent = [t["id"] for t in library[1:9]]  # ids 2..9
    ctx = {"recent_ids": recent, "recency_n": 8, "plan": "build"}
    for tid in recent:
        cand = next(t for t in library if t["id"] == tid)
        r = score(current, cand, ctx)
        assert r["excluded"] is True
    top = pick(current, library, ctx=ctx, k=3)
    top_ids = {p["track"]["id"] for p in top}
    assert top_ids.isdisjoint(set(recent))
    assert current["id"] not in top_ids


def test_fitting_requests_win(library):
    current = dict(library[0])
    current["bpm"] = 128.0
    current["camelot"] = "1A"
    # Candidate with matching BPM/key that is also requested
    requested = dict(library[2])
    requested["bpm"] = 128.0
    requested["camelot"] = "1A"
    requested["energy"] = current["energy"]
    other = dict(library[3])
    other["bpm"] = 128.0
    other["camelot"] = "1A"
    other["energy"] = current["energy"]
    ctx = {
        "plan": "build",
        "recent_ids": [],
        "request_ids": {requested["id"]},
        "weights": {
            "bpm_fit": 1.0,
            "camelot_fit": 1.0,
            "energy_fit": 1.0,
            "request_bonus": 1.5,
        },
    }
    top = pick(current, [requested, other], ctx=ctx, k=2)
    assert top[0]["track"]["id"] == requested["id"]
    assert any("requested" in r.lower() for r in top[0]["reasons"])


def test_pick_top3_with_reasons(library):
    current = library[0]
    ctx = {"plan": "peak", "recent_ids": [], "recency_n": 8}
    top = pick(current, library, ctx=ctx, k=3)
    assert len(top) == 3
    for row in top:
        assert "reason" in row and row["reason"]
        assert isinstance(row["reasons"], list) and row["reasons"]
        assert row["score"] > -100


def test_peak_prefers_higher_energy_than_warmup(library):
    current = library[5]
    base = {"recent_ids": [], "recency_n": 8, "request_ids": set()}
    peak = pick(current, library, ctx={**base, "plan": "peak"}, k=3)
    warm = pick(current, library, ctx={**base, "plan": "warmup"}, k=3)
    peak_e = sum(p["track"]["energy"] for p in peak) / 3
    warm_e = sum(p["track"]["energy"] for p in warm) / 3
    assert peak_e >= warm_e


@pytest.fixture
def client(tmp_path, monkeypatch, library):
    monkeypatch.setenv("DJ_BACKEND", "sim")
    monkeypatch.setattr("settings.SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr("settings.APP_DIR", tmp_path)
    import main
    from dj_controller import DJController
    from sentinel import set_killed
    from settings import save

    set_killed(False)
    save({"dj": {"automation": False, "manual_takeover": False, "plan": "build"}})
    main.dj = DJController(backend="sim")
    main.state["current_track"] = library[0]
    main.state["recent_track_ids"] = []
    main.state["queue"] = []
    main.state["next_suggestions"] = []
    monkeypatch.setattr(main, "all_tracks", lambda: list(library))
    os.environ.pop("OPERATOR_KEY", None)
    with TestClient(main.app) as c:
        yield c
    set_killed(False)


def test_get_dj_next_top3(client, library):
    r = client.get("/dj/next?k=3&plan=build")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["plan"] == "build"
    assert set(body["plans"]) == {"warmup", "build", "peak", "cooldown"}
    assert len(body["picks"]) == 3
    for p in body["picks"]:
        assert p["reason"]
        assert p["id"] != library[0]["id"]


def test_get_dj_next_respects_recency(client, library):
    import main

    main.state["recent_track_ids"] = [t["id"] for t in library[1:9]]
    r = client.get("/dj/next?k=3")
    ids = {p["id"] for p in r.json()["picks"]}
    assert ids.isdisjoint(set(main.state["recent_track_ids"]))


def test_get_dj_next_request_bonus(client, library):
    import main

    req_track = dict(library[2])
    req_track["bpm"] = library[0]["bpm"]
    req_track["camelot"] = library[0]["camelot"]
    main.state["queue"] = [
        {
            "id": 99,
            "query": req_track["filename"],
            "requester": "fan",
            "via": "twitch",
            "found": True,
            "track": req_track,
            "filename": req_track["filename"],
        }
    ]
    # Patch library so requested track shares BPM/camelot with current
    lib = list(library)
    lib[2] = req_track
    main.all_tracks = lambda: lib  # type: ignore
    r = client.get("/dj/next?k=3&plan=build")
    picks = r.json()["picks"]
    assert picks[0]["id"] == req_track["id"]
    assert "requested" in picks[0]["reason"].lower()


def test_announce_skipped_in_dj_mode(client, monkeypatch):
    from settings import save

    spoken: list = []

    def fake_speak(text, target=None, mood="talk"):
        spoken.append(text)

    import main

    monkeypatch.setattr(main, "queued_speak", fake_speak)
    save({"halo": {"dj_mode": True, "dj_mode_silent": False}})
    r = client.get("/dj/next?announce=true&k=3")
    assert r.status_code == 200
    assert r.json()["announced"] is False
    assert spoken == []

    save({"halo": {"dj_mode": False, "dj_mode_silent": False}})
    r2 = client.get("/dj/next?announce=true&k=3")
    assert r2.json()["announced"] is True
    assert spoken and "Next up" in spoken[0]


def test_bad_plan_422(client):
    r = client.get("/dj/next?plan=party")
    assert r.status_code == 422
