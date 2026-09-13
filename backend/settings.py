"""Persistent Halo / booth settings. Lives in Application Support, not the repo."""

from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path

LOCK = threading.Lock()

APP_DIR = Path.home() / "Library" / "Application Support" / "HaloDJ"
SETTINGS_PATH = APP_DIR / "settings.json"
VOICE_DIR = APP_DIR / "voice"

DEFAULTS = {
    "halo": {
        "name": "Halo",
        "persona": (
            "You are Halo, AI DJ co-host on Doc Weather's livestream "
            "(Charles Clottin / A Geostrophic Flow). Socialize with chat: greet by name, "
            "hype the music, take requests, answer short questions, introduce fans. "
            "1-2 spoken sentences. No hashtags, URLs, or 'as an AI'."
        ),
        "voice_provider": "macos",
        "voice_id": "Samantha",
        "elevenlabs_key": "",
        "elevenlabs_voice_id": "21m00Tcm4TlvDq8ikWAM",
        "dj_mode": False,
        "listen_mic": False,
        "capabilities": {
            "greet": True,
            "requests": True,
            "questions": True,
            "idle_talk": True,
            "hype": True,
            "introduce_fans": True,
            "midi": False,
        },
    },
    "environment": {
        "vibe": "night",
        "distraction": 30,
        "energy_mode": "balanced",
    },
    "dashboard": {
        "theme": "violet",
        "show_midi": True,
    },
    "room": {
        "allow_chat": True,
        "allow_requests": True,
        "allow_questions": True,
        "lan_open": False,
    },
    "demo": False,
    "youtube_id": "",
    "autonomy": 70,
    "streams": {
        "youtube": "https://www.youtube.com/@theweathermandj/live",
        "x": "https://x.com/charlesclottin",
        "facebook": "https://www.facebook.com/Charles.clottin",
        "tiktok": "https://www.tiktok.com/@charlesclottin/live",
        "rumble": "https://rumble.com/c/charlesclottin",
    },
}


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def ensure_dirs():
    APP_DIR.mkdir(parents=True, exist_ok=True)
    VOICE_DIR.mkdir(parents=True, exist_ok=True)


def load() -> dict:
    ensure_dirs()
    with LOCK:
        if not SETTINGS_PATH.exists():
            data = copy.deepcopy(DEFAULTS)
            SETTINGS_PATH.write_text(json.dumps(data, indent=2))
            return data
        try:
            raw = json.loads(SETTINGS_PATH.read_text())
        except Exception:
            raw = {}
        return _deep_merge(DEFAULTS, raw)


def save(patch: dict) -> dict:
    current = load()
    merged = _deep_merge(current, patch or {})
    env_key = os.getenv("ELEVENLABS_API_KEY")
    if env_key and not merged["halo"].get("elevenlabs_key"):
        merged["halo"]["elevenlabs_key"] = env_key
    ensure_dirs()
    with LOCK:
        SETTINGS_PATH.write_text(json.dumps(merged, indent=2))
    return merged


def public_settings(data: dict | None = None) -> dict:
    data = copy.deepcopy(data if data is not None else load())
    key = data.get("halo", {}).get("elevenlabs_key") or ""
    data["halo"]["elevenlabs_key_set"] = bool(key)
    data["halo"]["elevenlabs_key"] = ""
    return data
