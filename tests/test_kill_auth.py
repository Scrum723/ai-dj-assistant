"""Kill switch gating and operator auth on protected routes."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from main import app
    from sentinel import set_killed

    set_killed(False)
    with TestClient(app) as c:
        yield c
    set_killed(False)
    os.environ.pop("OPERATOR_KEY", None)


def test_kill_and_resume_action_play(client):
    r = client.post("/action/play", json={"deck": 1})
    assert r.status_code == 200
    assert r.json().get("status") != "killed"

    kill = client.post("/sentinel/kill-ours")
    assert kill.status_code == 200
    assert kill.json().get("killed") is True

    blocked = client.post("/action/play", json={"deck": 1})
    assert blocked.status_code == 200
    assert blocked.json() == {"status": "killed"}

    resume = client.post("/sentinel/resume")
    assert resume.status_code == 200
    assert resume.json().get("killed") is False

    ok = client.post("/action/play", json={"deck": 1})
    assert ok.status_code == 200
    assert ok.json().get("status") != "killed"


def test_say_noop_when_killed(agent, spoken):
    from sentinel import set_killed

    set_killed(True)
    agent._say("Should not speak this line at all", target="Fan", mood="talk")
    assert spoken == []
    set_killed(False)
    agent._say("Back online and talking now", target="Fan", mood="talk")
    assert len(spoken) == 1


def test_anonymous_action_play_401_when_operator_key_set(client):
    os.environ["OPERATOR_KEY"] = "secret-booth-key"
    try:
        r = client.post("/action/play", json={"deck": 1})
        assert r.status_code == 401

        ok = client.post(
            "/action/play",
            json={"deck": 1},
            headers={"X-Operator-Key": "secret-booth-key"},
        )
        assert ok.status_code == 200
    finally:
        os.environ.pop("OPERATOR_KEY", None)


def test_settings_and_agent_require_operator_when_key_set(client):
    os.environ["OPERATOR_KEY"] = "secret-booth-key"
    try:
        assert client.post("/settings", json={"demo": False}).status_code == 401
        assert client.post("/agent/demo", json={"enabled": False}).status_code == 401
        assert client.post("/sentinel/kill-ours").status_code == 401

        headers = {"X-Operator-Key": "secret-booth-key"}
        assert client.post("/settings", json={"demo": False}, headers=headers).status_code == 200
        assert client.post("/agent/demo", json={"enabled": False}, headers=headers).status_code == 200
        assert client.post("/sentinel/kill-ours", headers=headers).status_code == 200
        assert client.post("/sentinel/resume", headers=headers).status_code == 200
    finally:
        os.environ.pop("OPERATOR_KEY", None)


def test_localhost_allowed_when_operator_key_unset(client):
    os.environ.pop("OPERATOR_KEY", None)
    r = client.post("/action/play", json={"deck": 1})
    assert r.status_code == 200
