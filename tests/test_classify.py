"""Classify and request-parsing cases for Halo."""

from __future__ import annotations

import time

import pytest


@pytest.mark.parametrize(
    "message,expected",
    [
        ("play Mad World", "request"),
        ("!request Bloodrave", "request"),
        ("can you play some DNB", "request"),
        ("queue Synthetic Halo", "request"),
        ("what BPM is this?", "question"),
        ("where you streaming from?", "question"),
        ("hey halo what's up", "mention"),
        ("yo dj", "mention"),
        ("this slaps", "hype"),
        ("fire drop", "hype"),
        ("just hanging out", "chat"),
    ],
)
def test_classify_kinds(agent, message, expected):
    kind = agent._classify("Fan", message, platform="chat")
    assert kind == expected


def test_classify_welcome_new_viewer(agent):
    agent.viewers["NewFan"] = time.time()
    assert agent._classify("NewFan", "hello everyone", platform="chat") == "welcome"
    agent.viewers["SilentFan"] = time.time()
    assert agent._classify("SilentFan", "first time here", platform="chat") == "new_viewer"


def test_classify_returning(agent, tmp_memory):
    # Seed a prior-day visit
    tmp_memory.touch("youtube", "Regular")
    row = tmp_memory.get("youtube", "Regular")
    assert row is not None
    with tmp_memory._connect() as conn:
        conn.execute(
            "UPDATE viewers SET last_seen = ?, visits = 1 WHERE platform = ? AND handle = ?",
            (time.time() - 86400 * 2, "youtube", "Regular"),
        )
        conn.commit()

    agent.ingest("Regular", "hey I'm back", platform="youtube")
    assert "youtube:regular" in agent._returning
    kind = agent._classify("Regular", "hey I'm back", platform="youtube")
    assert kind == "returning"


@pytest.mark.parametrize(
    "message,query",
    [
        ("play Mad World", "Mad World"),
        ("!request Bloodrave", "Bloodrave"),
        ("can you play some DNB Bloodrave", "DNB Bloodrave"),
        ("queue Synthetic Halo!", "Synthetic Halo"),
        ("request night drive", "night drive"),
    ],
)
def test_request_query_parsing(agent, message, query):
    assert agent._request_query(message) == query
