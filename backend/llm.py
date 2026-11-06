"""LLM providers for Halo — Gemini and xAI, selected by CHIEF_PROVIDER (legacy HALO_PROVIDER)."""

from __future__ import annotations

import logging
import os
from typing import Optional, Protocol

import requests

from envcompat import env

logger = logging.getLogger(__name__)

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
XAI_MODEL_DEFAULT = "grok-2-latest"
XAI_BASE_URL = "https://api.x.ai/v1"


class LLMProvider(Protocol):
    @property
    def available(self) -> bool: ...

    def generate(self, system: str, prompt: str, max_tokens: int = 120) -> str: ...


class GeminiProvider:
    """Wraps google.genai Client — same call shape as the former cohost inline path."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.model = model or os.getenv("GEMINI_MODEL", GEMINI_MODEL)
        self._client = None
        key = api_key if api_key is not None else os.getenv("GEMINI_API_KEY")
        if not key:
            logger.warning("GEMINI_API_KEY missing — GeminiProvider unavailable.")
            return
        try:
            from google import genai

            self._client = genai.Client(api_key=key)
            logger.info("Halo Gemini client ready (%s).", self.model)
        except Exception as e:
            logger.error("Gemini init failed: %s", e)
            self._client = None

    @property
    def available(self) -> bool:
        return self._client is not None

    def generate(self, system: str, prompt: str, max_tokens: int = 120) -> str:
        if not self._client:
            return ""
        from google.genai import types

        # Gemini 3.x uses internal thinking tokens that count against max_output_tokens.
        # A low cap (e.g. 120) often finishes with MAX_TOKENS and empty visible text.
        out_tokens = max(int(max_tokens or 0), 512)
        config_kwargs = dict(
            system_instruction=system,
            temperature=0.8,
            max_output_tokens=out_tokens,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        try:
            # Prefer no/low thinking budget when the SDK supports it.
            config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        except Exception:
            pass
        response = self._client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(**config_kwargs),
        )
        text = (getattr(response, "text", None) or "").strip()
        if text:
            return text
        # Fallback: stitch text parts if .text is empty but candidates exist.
        try:
            for cand in response.candidates or []:
                parts = getattr(getattr(cand, "content", None), "parts", None) or []
                chunk = " ".join(
                    (getattr(p, "text", None) or "").strip() for p in parts if getattr(p, "text", None)
                ).strip()
                if chunk:
                    return chunk
        except Exception:
            pass
        return ""


class XaiProvider:
    """OpenAI-compatible chat completions against api.x.ai."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key if api_key is not None else os.getenv("XAI_API_KEY")
        self.model = model or os.getenv("XAI_MODEL") or XAI_MODEL_DEFAULT
        if not self.api_key:
            logger.warning("XAI_API_KEY missing — XaiProvider unavailable.")

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def generate(self, system: str, prompt: str, max_tokens: int = 120) -> str:
        if not self.api_key:
            return ""
        url = f"{XAI_BASE_URL}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.8,
        }
        resp = requests.post(url, headers=headers, json=body, timeout=30)
        if resp.status_code == 429:
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        return (message.get("content") or "").strip()


def get_provider(name: Optional[str] = None) -> LLMProvider:
    """Select provider via CHIEF_PROVIDER / legacy HALO_PROVIDER (default gemini)."""
    provider = (name or env("PROVIDER") or "gemini").strip().lower()
    if provider in {"xai", "grok"}:
        return XaiProvider()
    if provider != "gemini":
        logger.warning("Unknown CHIEF_PROVIDER/HALO_PROVIDER=%r — falling back to gemini.", provider)
    return GeminiProvider()
