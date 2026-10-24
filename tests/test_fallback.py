"""Fallback path — no API key / unavailable LLM uses local lines."""

from __future__ import annotations

import asyncio
import re
import time

from conftest import UnavailableLLM


def test_fallback_welcome(agent):
    line = agent._fallback("welcome", "Ada", "hi")
    assert "Ada" in line


def test_fallback_returning(agent):
    line = agent._fallback("returning", "Bea", "hey")
    assert "Bea" in line
    assert re.search(r"back|returned|again|pulled up", line, re.I)


def test_fallback_hype_question_chat(agent):
    assert "Cara" in agent._fallback("hype", "Cara", "fire")
    assert "Dan" in agent._fallback("question", "Dan", "why?")
    assert "Eve" in agent._fallback("chat", "Eve", "sup")


def test_compose_uses_fallback_when_llm_unavailable(agent):
    agent._llm = UnavailableLLM()
    line = asyncio.run(
        agent._compose("Fan", "what is this?", fallback="LOCAL FALLBACK LINE", hint="Answer briefly.")
    )
    assert line == "LOCAL FALLBACK LINE"


def test_compose_respects_cooldown(agent, fake_llm):
    agent._last_gemini = time.time()
    line = asyncio.run(
        agent._compose("Fan", "what is this?", fallback="COOLDOWN", hint="Answer.")
    )
    assert line == "COOLDOWN"
    assert fake_llm.calls == []


def test_handle_chat_fallback_without_ai(agent, spoken):
    agent._llm = UnavailableLLM()
    agent.ingest("NewPerson", "hello there", platform="chat")
    item = agent._inbox.get_nowait()
    asyncio.run(
        agent._handle_chat(item, autonomy=70, dj_mode=False, caps=agent._cfg()["halo"]["capabilities"])
    )
    assert spoken, "expected a spoken fallback reply"
    assert "NewPerson" in spoken[-1]["text"]
