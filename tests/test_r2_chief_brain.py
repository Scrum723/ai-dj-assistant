"""R2: cohost_name helper, canned lines, !chief/!halo commands, wake word."""

from __future__ import annotations

import pytest

from cohost import (
    AGENT_NAME,
    CHIEF_ON_RE,
    CHIEF_QUIET_RE,
    HALO_ON_RE,
    HALO_QUIET_RE,
    IDLE_GENERIC,
    IDLE_WAITING_ROOM,
    MENTION_RE,
    PERSONA,
    PERSONA_PRESETS,
)
from settings import cohost_name


@pytest.fixture
def settings_env(tmp_path, monkeypatch):
    monkeypatch.setattr("settings.SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr("settings.APP_DIR", tmp_path)
    monkeypatch.setattr("settings.VOICE_DIR", tmp_path / "voice")
    return tmp_path


def test_cohost_name_helper(settings_env):
    from settings import load, save

    data = load()
    assert cohost_name(data) == "Chief"
    assert cohost_name(None) == "Chief"
    assert AGENT_NAME == "Chief"

    save({"halo": {"name": "DJ Sparky"}})
    assert cohost_name(load()) == "DJ Sparky"
    assert cohost_name({"halo": {}}) == "Chief"


def test_canned_templates_have_no_hardcoded_halo():
    """Static canned/persona templates must not hard-code Halo."""
    blob = "\n".join(
        [
            PERSONA,
            *PERSONA_PRESETS.values(),
            *IDLE_GENERIC,
            *IDLE_WAITING_ROOM,
        ]
    )
    assert "Halo" not in blob
    assert "{name}" in PERSONA
    assert any("{name}" in line for line in IDLE_GENERIC)
    assert any("{name}" in line for line in IDLE_WAITING_ROOM)


def test_idle_and_fallback_use_configured_name(agent, spoken):
    base = agent.get_settings()
    # Avoid fixture track "Synthetic Halo.mp3" leaking into idle copy.
    agent.get_snapshot = lambda: {"current_track": {"filename": "Night Drive.mp3"}, "queue": []}

    def gs_live():
        return {
            **base,
            "halo": {**base["halo"], "name": "Chief", "live": True},
        }

    agent.get_settings = gs_live
    named = [line.format(name="Chief") for line in IDLE_GENERIC]
    assert any("Chief" in line for line in named)
    assert all("halo" not in line.lower() for line in named)

    agent._idle_line(70, agent._cfg()["halo"]["capabilities"])
    assert spoken
    for s in spoken:
        assert "halo" not in (s["text"] or "").lower()

    spoken.clear()

    def gs_wait():
        return {
            **base,
            "halo": {**base["halo"], "name": "Chief", "live": False},
        }

    agent.get_settings = gs_wait
    wait_named = [line.format(name="Chief") for line in IDLE_WAITING_ROOM]
    assert any("Chief" in line for line in wait_named)
    assert all("halo" not in line.lower() for line in wait_named)

    agent._idle_line(70, agent._cfg()["halo"]["capabilities"])
    assert spoken
    for s in spoken:
        assert "halo" not in (s["text"] or "").lower()

    line = agent._fallback("welcome", "Ada", "hi")
    assert "Ada" in line
    assert "halo" not in line.lower()


def test_persona_uses_configured_name(agent):
    base = agent.get_settings()

    def gs():
        return {**base, "halo": {**base["halo"], "name": "Chief", "persona_preset": "hype"}}

    agent.get_settings = gs
    persona = agent._persona()
    assert persona.startswith("You are Chief,")
    assert "Halo" not in persona


def test_chief_and_halo_quiet_on_commands(agent, spoken):
    assert HALO_QUIET_RE.match("!halo quiet")
    assert HALO_QUIET_RE.match("!chief quiet")
    assert CHIEF_QUIET_RE.match("!chief quiet")
    assert HALO_ON_RE.match("!halo on")
    assert HALO_ON_RE.match("!chief on")
    assert CHIEF_ON_RE.match("!chief on")

    agent.ingest("charles", "!chief quiet", platform="dashboard")
    assert agent.quiet is True
    agent.ingest("charles", "!chief on", platform="dashboard")
    assert agent.quiet is False

    agent.ingest("charles", "!halo quiet", platform="dashboard")
    assert agent.quiet is True
    agent.ingest("charles", "!halo on", platform="dashboard")
    assert agent.quiet is False


def test_wake_word_chief_like_halo(agent):
    assert MENTION_RE.search("hey chief what's up")
    assert MENTION_RE.search("hey halo what's up")
    assert agent._classify("Fan", "hey chief", platform="chat") == "mention"
    assert agent._classify("Fan", "hey halo", platform="chat") == "mention"


def test_intro_line_uses_name(agent, spoken):
    base = agent.get_settings()

    def gs():
        return {**base, "halo": {**base["halo"], "name": "Chief"}}

    agent.get_settings = gs
    name = agent._halo_name()
    assert name == "Chief"
    agent._say(f"{name} in the booth. Chat with me.", mood="hype")
    assert spoken
    assert "Chief" in spoken[-1]["text"]
    assert "Halo" not in spoken[-1]["text"]
