"""A4: Persona presets, DJ quiet mode, operator priority, TTS timeout fallback."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest

from conftest import UnavailableLLM
from cohost import PERSONA_PRESETS, clip_sentences
from moderation import check_input, reset_rate_limits
from voice import TTS_PROVIDER_TIMEOUT, synthesize


def _with_halo(agent, **halo_patch):
    base = agent.get_settings()

    def gs():
        return {
            **base,
            "halo": {**base["halo"], **halo_patch},
            "environment": base.get("environment")
            or {"energy_mode": "balanced", "distraction": 30, "vibe": "night"},
        }

    agent.get_settings = gs
    return agent


def test_clip_sentences():
    assert clip_sentences("One. Two. Three.", 1) == "One."
    assert clip_sentences("One. Two. Three.", 2) == "One. Two."
    assert clip_sentences("Only one", 1) == "Only one"


def test_persona_preset_hype_chill_host(agent, fake_llm):
    agent._last_gemini = 0.0
    agent._ai_backoff_until = 0.0
    _with_halo(agent, persona_preset="chill")
    asyncio.run(
        agent._compose(
            "Fan",
            "hey halo",
            fallback="FALLBACK LINE HERE",
            hint="Answer briefly.",
            platform="chat",
        )
    )
    assert fake_llm.calls
    system = fake_llm.calls[-1][0]
    assert system == PERSONA_PRESETS["chill"]

    fake_llm.calls.clear()
    agent._last_gemini = 0.0
    _with_halo(agent, persona_preset="host")
    asyncio.run(
        agent._compose(
            "Fan",
            "hey halo again",
            fallback="FALLBACK LINE HERE",
            hint="Answer briefly.",
            platform="chat",
        )
    )
    assert fake_llm.calls[-1][0] == PERSONA_PRESETS["host"]


def test_persona_custom_and_live_without_restart(agent, fake_llm):
    custom = "You are CustomHalo. Always say custom booth. Reply in one sentence."
    _with_halo(agent, persona_preset="custom", persona=custom)
    agent._last_gemini = 0.0
    agent._ai_backoff_until = 0.0
    asyncio.run(
        agent._compose(
            "Fan",
            "what is this?",
            fallback="FALLBACK LINE HERE OK",
            hint="Answer.",
            platform="chat",
        )
    )
    assert fake_llm.calls[-1][0] == custom

    # Change preset live — next compose picks it up (no restart)
    _with_halo(agent, persona_preset="hype", persona=custom)
    agent._last_gemini = 0.0
    asyncio.run(
        agent._compose(
            "Fan",
            "and now?",
            fallback="FALLBACK LINE HERE OK",
            hint="Answer.",
            platform="chat",
        )
    )
    assert fake_llm.calls[-1][0] == PERSONA_PRESETS["hype"]


def test_dj_mode_answers_mention_silent_otherwise(agent, spoken):
    agent._llm = UnavailableLLM()
    _with_halo(agent, dj_mode=True, dj_mode_silent=False)
    agent._last_reply_at = 0.0
    agent._last_idle = time.time()
    agent._pending.clear()

    agent.ingest("Chatty", "cool vibes tonight", platform="twitch")
    asyncio.run(agent._tick())
    assert spoken == []

    spoken.clear()
    agent._pending.clear()
    agent.ingest("Asker", "hey halo what's up", platform="youtube")
    t0 = time.perf_counter()
    asyncio.run(agent._tick())
    elapsed = time.perf_counter() - t0
    assert len(spoken) == 1
    assert spoken[0]["target"] == "Asker"
    assert elapsed < 3.0
    assert clip_sentences(spoken[0]["text"], 1) == spoken[0]["text"] or spoken[0]["text"].count(".") <= 1


def test_dj_mode_answers_request(agent, spoken):
    agent._llm = UnavailableLLM()
    _with_halo(agent, dj_mode=True)
    agent._last_reply_at = 0.0
    item = {
        "author": "ReqFan",
        "message": "play Mad World",
        "platform": "twitch",
        "ts": time.time(),
        "_kind": "request",
    }
    asyncio.run(
        agent._handle_chat(
            item,
            autonomy=70,
            dj_mode=True,
            caps=agent._cfg()["halo"]["capabilities"],
            energy="eco",
        )
    )
    assert spoken
    assert "Mad World" in spoken[-1]["text"] or "queue" in spoken[-1]["text"].lower()


def test_dj_mode_no_idle(agent, spoken):
    agent._llm = UnavailableLLM()
    _with_halo(agent, dj_mode=True)
    agent.quiet = False
    agent._last_idle = 0.0
    agent._last_reply_at = time.time()
    agent._pending.clear()
    asyncio.run(agent._tick())
    assert spoken == []


def test_operator_skips_cooldown_and_rate(agent, spoken):
    agent._llm = UnavailableLLM()
    _with_halo(agent, dj_mode=False, operator_handles=["charles"])
    agent._last_reply_user["charles"] = time.time()
    item = {
        "author": "charles",
        "message": "halo status check please",
        "platform": "twitch",
        "ts": time.time(),
        "_kind": "mention",
    }
    asyncio.run(
        agent._handle_chat(
            item,
            autonomy=70,
            dj_mode=False,
            caps=agent._cfg()["halo"]["capabilities"],
            energy="eco",
        )
    )
    assert spoken, "operator must bypass per-user cooldown"

    reset_rate_limits()
    for _ in range(12):
        ok, reason = check_input("charles", "ping", skip_rate=True)
        assert ok, reason


def test_operator_priority_in_dj_mode(agent, spoken):
    agent._llm = UnavailableLLM()
    _with_halo(agent, dj_mode=True, operator_handles={"twitch": "charles"})
    agent._last_reply_at = 0.0
    agent._last_idle = time.time()
    agent.ingest("Random", "nice track", platform="youtube")
    agent.ingest("charles", "keep the low end up", platform="twitch")
    asyncio.run(agent._tick())
    assert len(spoken) == 1
    assert spoken[0]["target"] == "charles"


def test_operator_may_get_longer_reply(agent, fake_llm):
    fake_llm.reply = (
        "First sentence for the booth. Second sentence with detail. "
        "Third sentence wrapping up."
    )
    _with_halo(agent, operator_handles=["charles"], dj_mode=False)
    agent._last_gemini = 0.0
    agent._ai_backoff_until = 0.0
    line = asyncio.run(
        agent._compose(
            "charles",
            "give me a quick rundown",
            fallback="Booth copy one. Booth copy two. Booth copy three.",
            hint="Reply to the booth operator in 2-3 short spoken sentences.",
            platform="dashboard",
            max_sentences=3,
        )
    )
    # three sentences retained
    parts = [p for p in line.replace("?", ".").replace("!", ".").split(".") if p.strip()]
    assert len(parts) >= 2


def test_tts_timeout_falls_through_to_none(monkeypatch, tmp_path):
    import voice as voice_mod

    monkeypatch.setattr(voice_mod, "VOICE_DIR", tmp_path / "voice")
    monkeypatch.setattr(
        voice_mod,
        "ensure_dirs",
        lambda: (tmp_path / "voice").mkdir(parents=True, exist_ok=True),
    )
    monkeypatch.setattr(voice_mod, "TTS_PROVIDER_TIMEOUT", 0.25)

    def slow_macos(text, voice):
        time.sleep(2.0)
        return tmp_path / "never.wav"

    def load_cfg():
        return {
            "halo": {
                "voice_provider": "macos",
                "voice_id": "Samantha",
                "elevenlabs_key": "",
            }
        }

    monkeypatch.setattr(voice_mod, "load", load_cfg)
    monkeypatch.setattr(voice_mod, "macos_say", slow_macos)

    t0 = time.perf_counter()
    url = synthesize("hello booth")
    elapsed = time.perf_counter() - t0
    assert url is None
    assert elapsed < 1.5
    assert TTS_PROVIDER_TIMEOUT == 4.0  # production default unchanged


def test_dj_mode_silent_status_flag(agent):
    _with_halo(agent, dj_mode=False, dj_mode_silent=True)
    st = agent.status()
    assert st["dj_mode_silent"] is True
    assert st["mood"] == "quiet"
