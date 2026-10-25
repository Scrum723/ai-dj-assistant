"""Prompt includes viewer memory line."""

from __future__ import annotations

import asyncio
import time


def test_viewer_memory_line(agent, tmp_memory):
    tmp_memory.touch("chat", "Fan")
    with tmp_memory._connect() as conn:
        conn.execute(
            "UPDATE viewers SET visits = 5, last_request = ? WHERE platform = ? AND handle = ?",
            ("Mad World", "chat", "fan"),
        )
        conn.commit()
    line = agent._viewer_memory_line("FAN", "chat")
    assert line == "Viewer memory: 5th visit, last asked for Mad World"


def test_compose_prompt_includes_memory(agent, fake_llm, tmp_memory):
    tmp_memory.touch("chat", "Fan")
    with tmp_memory._connect() as conn:
        conn.execute(
            "UPDATE viewers SET visits = 5, last_request = ? WHERE platform = ? AND handle = ?",
            ("Mad World", "chat", "fan"),
        )
        conn.commit()
    agent._last_gemini = 0.0
    agent._ai_backoff_until = 0.0

    text = asyncio.run(
        agent._compose(
            "Fan",
            "what is playing?",
            fallback="FALLBACK",
            hint="Answer briefly if you can.",
            platform="chat",
        )
    )
    assert text != "FALLBACK"
    assert fake_llm.calls, "expected LLM generate call"
    _system, prompt, _max = fake_llm.calls[-1]
    assert "Viewer memory: 5th visit, last asked for Mad World" in prompt
    assert "Fan" in prompt
    assert "what is playing?" in prompt


def test_ingest_touch_and_remember_on_request(agent, tmp_memory, spoken):
    agent.ingest("Requester", "play Night Drive", platform="twitch")
    item = agent._inbox.get_nowait()
    asyncio.run(
        agent._handle_chat(
            item,
            autonomy=70,
            dj_mode=False,
            caps=agent._cfg()["halo"]["capabilities"],
            energy="eco",  # skip AI path; still remember request
        )
    )
    got = tmp_memory.get("twitch", "Requester")
    assert got is not None
    assert got.last_request == "Night Drive"
    assert got.visits >= 1
