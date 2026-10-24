"""B8: streaks/badges, /shows/next, /healthz without mido, notify store-only."""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from memory import (
    ViewerMemory,
    next_streak,
    retention_badges,
    retention_summary,
)


def test_retention_badge_thresholds():
    assert retention_badges(0) == []
    assert retention_badges(2) == []
    assert retention_badges(3) == ["regular"]
    assert retention_badges(9) == ["regular"]
    assert retention_badges(10) == ["regular", "loyal"]
    assert retention_badges(25) == ["regular", "loyal", "legend"]
    assert retention_summary(10, streak=4) == {
        "visits": 10,
        "streak": 4,
        "badges": ["regular", "loyal"],
    }


def test_next_streak_math():
    now = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc).timestamp()
    yesterday = datetime(2026, 3, 9, 18, 0, tzinfo=timezone.utc).timestamp()
    two_days = datetime(2026, 3, 8, 18, 0, tzinfo=timezone.utc).timestamp()
    today_earlier = datetime(2026, 3, 10, 1, 0, tzinfo=timezone.utc).timestamp()

    assert next_streak(None, 0, now) == 1
    assert next_streak(yesterday, 3, now) == 4
    assert next_streak(two_days, 5, now) == 1
    assert next_streak(today_earlier, 2, now) == 2


def test_streak_and_badges_across_linked_platforms(tmp_path):
    mem = ViewerMemory(db_path=tmp_path / "halo.db")
    day = 86_400

    mem.touch("twitch", "Kim", profile_id=42)
    with mem._connect() as conn:
        conn.execute(
            "UPDATE viewers SET last_seen = ?, visits = 2, streak = 2 WHERE platform = ? AND handle = ?",
            (time.time() - day, "twitch", "kim"),
        )
        conn.commit()

    rec, returning = mem.touch("youtube", "KimYT", profile_id=42)
    assert returning is True
    assert rec.visits >= 3
    assert rec.streak >= 3
    assert "regular" in rec.badges()

    with mem._connect() as conn:
        conn.execute(
            "UPDATE viewers SET visits = 9, last_seen = ? WHERE profile_id = ?",
            (time.time() - day, "42"),
        )
        conn.commit()
    rec2, _ = mem.touch("twitch", "Kim", profile_id=42)
    assert rec2.visits == 10
    assert rec2.badges() == ["regular", "loyal"]
    summary = mem.retention_for(profile_id=42)
    assert summary["visits"] == 10
    assert "loyal" in summary["badges"]


def test_next_show_payload_skips_past():
    from main import next_show_payload

    soon = (datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0)
    past = (datetime.now(timezone.utc) - timedelta(days=1)).replace(microsecond=0)
    cfg = {
        "shows": [
            {"start_iso": past.isoformat().replace("+00:00", "Z"), "title": "Old"},
            {"start_iso": soon.isoformat().replace("+00:00", "Z"), "title": "Friday Drop"},
        ]
    }
    body = next_show_payload(cfg)
    assert body["next"]["title"] == "Friday Drop"


@pytest.fixture(scope="module")
def client():
    os.environ.pop("OPERATOR_KEY", None)
    from main import app, agent
    from sentinel import set_killed

    set_killed(False)
    if agent:
        agent.quiet = True
    with TestClient(app) as c:
        yield c
    set_killed(False)


def test_healthz_without_requiring_midi(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert "midi" in data
    assert "llm" in data
    assert isinstance(data["adapters"], dict)
    # Must stay 200 even when midi is false (no mido on cloud)
    assert data["midi"] in (True, False)


def test_me_notify_store_only(client):
    email = f"b8-{uuid.uuid4().hex[:10]}@example.com"
    su = client.post(
        "/auth/signup",
        json={"display_name": "B8Fan", "email": email, "password": "password123"},
    )
    assert su.status_code == 200, su.text
    n = client.post("/me/notify", json={"email_opt_in": True})
    assert n.status_code == 200
    assert n.json()["email_opt_in"] is True
    me = client.get("/me/profile")
    assert me.status_code == 200
    body = me.json()
    assert body["user"]["email_opt_in"] is True
    assert "streak" in body
    assert "badges" in body
    assert "visits" in body
