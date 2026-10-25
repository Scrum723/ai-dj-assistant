"""Cohost wires moderation, operator commands, and output filtering."""

from __future__ import annotations

import asyncio

from moderation import is_muted


def test_blocked_input_never_reaches_llm(agent, fake_llm, spoken):
    before = len(fake_llm.calls)
    agent.ingest("RudeFan", "you are a retard", platform="chat")
    assert agent._inbox.empty()
    assert len(fake_llm.calls) == before
    assert spoken == []


def test_output_filter_falls_back_when_llm_dirty(agent, fake_llm, spoken):
    fake_llm.reply = "what a retard take honestly"
    agent._last_gemini = 0.0
    agent._ai_backoff_until = 0.0
    line = asyncio.run(
        agent._compose(
            "Fan",
            "what do you think?",
            fallback="Good question Fan. Stay with the mix.",
            hint="Answer briefly.",
            platform="chat",
        )
    )
    assert "retard" not in line.lower()
    assert "Fan" in line or "mix" in line.lower()


def test_operator_mute_unmute_and_quiet(agent, spoken):
    agent.ingest("charles", "!mute SpamBot", platform="dashboard")
    assert is_muted("SpamBot")
    agent.ingest("SpamBot", "buy followers now", platform="chat")
    assert agent._inbox.empty()

    agent.ingest("charles", "!unmute SpamBot", platform="dashboard")
    assert not is_muted("SpamBot")

    agent.ingest("charles", "!halo quiet", platform="dashboard")
    assert agent.quiet is True
    agent.ingest("charles", "!halo on", platform="dashboard")
    assert agent.quiet is False
    assert any("quiet" in (s["text"] or "").lower() or "back on" in (s["text"] or "").lower() for s in spoken)
