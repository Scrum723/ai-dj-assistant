"""Halo voice: macOS neural/say first, optional ElevenLabs, browser last."""

from __future__ import annotations

import logging
import re
import subprocess
import time
import uuid
from pathlib import Path

import requests

from settings import VOICE_DIR, ensure_dirs, load

logger = logging.getLogger(__name__)

MAC_VOICES = [
    "Samantha",
    "Ava",
    "Zoe",
    "Nicky",
    "Allison",
    "Susan",
    "Karen",
    "Kate",
    "Moira",
    "Tessa",
]


def _safe_name(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "", text)[:40] or "halo"


def macos_say(text: str, voice: str) -> Path | None:
    ensure_dirs()
    out = VOICE_DIR / f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}.wav"
    voice = voice if voice in MAC_VOICES else "Samantha"
    try:
        subprocess.run(
            ["say", "-v", voice, "-o", str(out), "--data-format=LEI16@22050", text],
            check=True,
            capture_output=True,
            timeout=20,
        )
        if out.exists() and out.stat().st_size > 44:
            return out
    except Exception as e:
        logger.error("macOS say failed: %s", e)
    return None


def elevenlabs_tts(text: str, api_key: str, voice_id: str) -> Path | None:
    ensure_dirs()
    out = VOICE_DIR / f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}.mp3"
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    try:
        res = requests.post(
            url,
            headers={
                "xi-api-key": api_key,
                "accept": "audio/mpeg",
                "content-type": "application/json",
            },
            json={
                "text": text,
                "model_id": "eleven_turbo_v2_5",
                "voice_settings": {"stability": 0.45, "similarity_boost": 0.75},
            },
            timeout=20,
        )
        res.raise_for_status()
        out.write_bytes(res.content)
        return out
    except Exception as e:
        logger.error("ElevenLabs TTS failed: %s", e)
        return None


def synthesize(text: str) -> str | None:
    """Return a /voice/<file> URL or None to let the overlay use browser TTS."""
    text = (text or "").strip()
    if not text:
        return None
    cfg = load()["halo"]
    provider = cfg.get("voice_provider") or "macos"
    if provider == "browser":
        return None
    if provider == "elevenlabs":
        key = cfg.get("elevenlabs_key") or ""
        voice_id = cfg.get("elevenlabs_voice_id") or "21m00Tcm4TlvDq8ikWAM"
        if key:
            path = elevenlabs_tts(text, key, voice_id)
            if path:
                return f"/voice/{path.name}"
        logger.info("ElevenLabs unavailable — falling back to macOS say.")
    path = macos_say(text, cfg.get("voice_id") or "Samantha")
    if path:
        return f"/voice/{path.name}"
    return None


def resolve_voice_file(name: str) -> Path | None:
    safe = Path(name).name
    if safe != name or ".." in name:
        return None
    path = VOICE_DIR / safe
    if path.exists() and path.is_file():
        return path
    return None
