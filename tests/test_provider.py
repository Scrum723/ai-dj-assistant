"""HALO_PROVIDER selection — no live API calls."""

from __future__ import annotations

import os

import pytest

from llm import GeminiProvider, XaiProvider, get_provider


def test_default_provider_is_gemini(monkeypatch):
    monkeypatch.delenv("HALO_PROVIDER", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    p = get_provider()
    assert isinstance(p, GeminiProvider)
    assert p.available is False


def test_provider_gemini_explicit(monkeypatch):
    monkeypatch.setenv("HALO_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    p = get_provider()
    assert isinstance(p, GeminiProvider)


def test_provider_xai(monkeypatch):
    monkeypatch.setenv("HALO_PROVIDER", "xai")
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    p = get_provider()
    assert isinstance(p, XaiProvider)
    assert p.available is False


def test_provider_grok_alias(monkeypatch):
    monkeypatch.setenv("HALO_PROVIDER", "grok")
    p = get_provider()
    assert isinstance(p, XaiProvider)


def test_unknown_provider_falls_back_to_gemini(monkeypatch):
    monkeypatch.setenv("HALO_PROVIDER", "nope")
    p = get_provider()
    assert isinstance(p, GeminiProvider)


def test_xai_respects_model_env(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("XAI_MODEL", "grok-test-model")
    p = XaiProvider()
    assert p.available is True
    assert p.model == "grok-test-model"


def test_get_provider_name_override(monkeypatch):
    monkeypatch.setenv("HALO_PROVIDER", "gemini")
    p = get_provider("xai")
    assert isinstance(p, XaiProvider)
