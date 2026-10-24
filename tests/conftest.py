"""Pytest fixtures for Halo / CohostAgent — stubs, no API keys."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# Offline tests — never call real providers
os.environ.pop("GEMINI_API_KEY", None)
os.environ.pop("XAI_API_KEY", None)
os.environ.setdefault("HALO_PROVIDER", "gemini")


class FakeLLM:
    """Records generate() calls; returns a long-enough canned reply."""

    def __init__(self, reply: str = "Hey there friend, the booth hears you loud and clear tonight."):
        self.reply = reply
        self.calls: list[tuple[str, str, int]] = []
        self._available = True

    @property
    def available(self) -> bool:
        return self._available

    def generate(self, system: str, prompt: str, max_tokens: int = 120) -> str:
        self.calls.append((system, prompt, max_tokens))
        return self.reply


class UnavailableLLM:
    @property
    def available(self) -> bool:
        return False

    def generate(self, system: str, prompt: str, max_tokens: int = 120) -> str:
        return ""


@pytest.fixture
def tmp_memory(tmp_path):
    from memory import ViewerMemory

    return ViewerMemory(db_path=tmp_path / "halo.db")


@pytest.fixture
def fake_llm():
    return FakeLLM()


@pytest.fixture
def spoken():
    return []


@pytest.fixture
def noted():
    return []


@pytest.fixture
def agent(tmp_memory, fake_llm, spoken, noted):
    from cohost import CohostAgent

    def get_snapshot():
        return {
            "current_track": {"filename": "Synthetic Halo.mp3"},
            "queue": [],
        }

    def search_tracks(q: str):
        return [{"filename": "Mad World.mp3", "path": "/t/mad.mp3"}]

    def enqueue_request(*, query, requester, via="chat", silent=True):
        return {
            "status": "queued",
            "item": {"filename": f"{query}.mp3", "query": query, "requester": requester},
        }

    def speak(text, target=None, mood="talk"):
        spoken.append({"text": text, "target": target, "mood": mood})

    def note_chat(item):
        noted.append(item)

    def get_autonomy():
        return 70

    def get_settings():
        return {
            "demo": False,
            "autonomy": 70,
            "halo": {
                "name": "Halo",
                "dj_mode": False,
                "capabilities": {
                    "greet": True,
                    "questions": True,
                    "hype": True,
                    "requests": True,
                    "idle_talk": True,
                    "introduce_fans": True,
                },
            },
            "environment": {"energy_mode": "balanced", "distraction": 30, "vibe": "night"},
        }

    a = CohostAgent(
        get_snapshot=get_snapshot,
        search_tracks=search_tracks,
        enqueue_request=enqueue_request,
        speak=speak,
        note_chat=note_chat,
        get_autonomy=get_autonomy,
        get_settings=get_settings,
        memory=tmp_memory,
        llm=fake_llm,
    )
    # Allow immediate LLM use in tests
    a._last_gemini = 0.0
    a._ai_backoff_until = 0.0
    return a
