"""R4: CHIEF_* env preferred; HALO_* legacy; bare PORT wins."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from envcompat import env
from llm import get_provider
from trainer import training_interval_sec


@pytest.fixture(autouse=True)
def _clear_alias_env(monkeypatch):
    for key in (
        "PORT",
        "CHIEF_PORT",
        "HALO_PORT",
        "CHIEF_HOST",
        "HALO_HOST",
        "CHIEF_PROVIDER",
        "HALO_PROVIDER",
        "CHIEF_MODEL",
        "HALO_MODEL",
        "CHIEF_TRAINING_LEVEL",
        "HALO_TRAINING_LEVEL",
        "CHIEF_COST_PER_1K",
        "HALO_COST_PER_1K",
        "CHIEF_DAILY_CALL_CAP",
        "HALO_DAILY_CALL_CAP",
        "CHIEF_DESKTOP",
        "HALO_DESKTOP",
        "GEMINI_MODEL",
        "XAI_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)
    yield


def test_env_chief_wins_over_halo(monkeypatch):
    monkeypatch.setenv("CHIEF_PROVIDER", "xai")
    monkeypatch.setenv("HALO_PROVIDER", "gemini")
    assert env("PROVIDER") == "xai"


def test_env_halo_alone_works(monkeypatch):
    monkeypatch.setenv("HALO_PROVIDER", "xai")
    assert env("PROVIDER") == "xai"


def test_env_default_when_unset():
    assert env("PROVIDER", "gemini") == "gemini"
    assert env("MISSING") is None


def test_get_provider_uses_chief(monkeypatch):
    monkeypatch.setenv("CHIEF_PROVIDER", "xai")
    monkeypatch.setenv("HALO_PROVIDER", "gemini")
    p = get_provider()
    assert type(p).__name__ == "XaiProvider"


def test_get_provider_legacy_halo(monkeypatch):
    monkeypatch.setenv("HALO_PROVIDER", "xai")
    p = get_provider()
    assert type(p).__name__ == "XaiProvider"


def test_training_level_chief(monkeypatch):
    monkeypatch.setenv("CHIEF_TRAINING_LEVEL", "3")
    monkeypatch.setenv("HALO_TRAINING_LEVEL", "1")
    assert training_interval_sec() == 6 * 60 * 60


def test_training_model_legacy_halo(monkeypatch):
    monkeypatch.setenv("HALO_MODEL", "grok-3-mini")
    assert training_interval_sec() == 4 * 60 * 60


def test_cost_and_cap_via_cohost(monkeypatch):
    from cohost import CohostAgent

    monkeypatch.setenv("CHIEF_COST_PER_1K", "0.05")
    monkeypatch.setenv("HALO_COST_PER_1K", "0.01")
    monkeypatch.setenv("CHIEF_DAILY_CALL_CAP", "7")
    monkeypatch.setenv("HALO_DAILY_CALL_CAP", "99")
    agent = CohostAgent.__new__(CohostAgent)
    assert agent._cost_per_1k() == 0.05
    assert agent._daily_call_cap() == 7


def test_cap_legacy_halo_alone(monkeypatch):
    from cohost import CohostAgent

    monkeypatch.setenv("HALO_DAILY_CALL_CAP", "3")
    agent = CohostAgent.__new__(CohostAgent)
    assert agent._daily_call_cap() == 3


def test_main_port_bare_wins(monkeypatch):
    monkeypatch.setenv("PORT", "9999")
    monkeypatch.setenv("CHIEF_PORT", "1111")
    monkeypatch.setenv("HALO_PORT", "2222")
    import main

    assert main.room_urls()["local"].startswith("http://127.0.0.1:9999/")


def test_main_port_chief_over_halo(monkeypatch):
    monkeypatch.setenv("CHIEF_PORT", "8111")
    monkeypatch.setenv("HALO_PORT", "8222")
    import main

    assert main.room_urls()["local"].startswith("http://127.0.0.1:8111/")


def test_main_port_halo_alone(monkeypatch):
    monkeypatch.setenv("HALO_PORT", "8333")
    import main

    assert main.room_urls()["local"].startswith("http://127.0.0.1:8333/")


def test_desktop_inline_lookup_matches_envcompat(monkeypatch):
    """desktop.py cannot import envcompat before sys.path; mirror the 3-line lookup."""
    monkeypatch.setenv("CHIEF_PORT", "8444")
    monkeypatch.setenv("HALO_PORT", "8555")
    _port = os.getenv("CHIEF_PORT")
    if _port is None:
        _port = os.getenv("HALO_PORT", "8000")
    assert int(os.getenv("PORT") or _port) == 8444
    assert env("PORT") == "8444"

    monkeypatch.delenv("CHIEF_PORT", raising=False)
    _port = os.getenv("CHIEF_PORT")
    if _port is None:
        _port = os.getenv("HALO_PORT", "8000")
    assert int(os.getenv("PORT") or _port) == 8555

    monkeypatch.setenv("PORT", "9000")
    assert int(os.getenv("PORT") or _port) == 9000


def test_desktop_sets_both_desktop_flags():
    src = (Path(__file__).resolve().parents[1] / "desktop.py").read_text(encoding="utf-8")
    assert 'setdefault("CHIEF_DESKTOP", "1")' in src
    assert 'setdefault("HALO_DESKTOP", "1")' in src
