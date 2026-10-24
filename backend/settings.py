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
        "persona_preset": "hype",
        "persona": (
            "You are Halo, the LLM DJ buddy inside DJ Bot Botty "
            "(Charles Clottin / A Geostrophic Flow). Socialize with chat: greet by name, "
            "hype the music, take requests, answer short questions, introduce fans. "
            "1-2 spoken sentences. No hashtags, URLs, or 'as an AI'."
        ),
        "voice_provider": "macos",
        "voice_id": "Samantha",
        "elevenlabs_key": "",
        "elevenlabs_voice_id": "21m00Tcm4TlvDq8ikWAM",
        "dj_mode": False,
        "dj_mode_silent": False,
        "listen_mic": False,
        "live": True,
        "min_reply_sec": 8,
        "max_replies_per_min": 6,
        "user_cooldown_sec": 20,
        "task_ttl_sec": 120,
        "priorities": {
            "operator": 0,
            "safety": 1,
            "supporter": 2,
            "request": 3,
            "mention": 4,
            "first_timer": 5,
            "regular": 6,
            "idle": 9,
        },
        "schedule": "",
        "next_show": "",
        "last_set": "",
        "operator_handles": ["charles", "charlesclottin"],
        "say_amounts": False,
        "thanks": {
            "youtube": "Thank you {name} on YouTube{amount_bit} — the booth feels that!",
            "twitch": "Huge thanks {name} from Twitch{amount_bit} — appreciate the support!",
            "kick": "Thanks {name}!",
            "tiktok": "Thanks {name}!",
            "default": "Thank you {name} for the support!",
        },
        "capabilities": {
            "greet": True,
            "requests": True,
            "questions": True,
            "idle_talk": True,
            "hype": True,
            "introduce_fans": True,
            "midi": False,
        },
        "segments": [
            {"name": "roll_call", "every_min": 45, "enabled": True, "dj_mode_ok": True},
            {"name": "request_hour", "at_minute": 0, "enabled": True, "dj_mode_ok": False},
            {"name": "guess_the_bpm", "every_min": 30, "enabled": True, "dj_mode_ok": False},
            {"name": "track_id", "enabled": True, "dj_mode_ok": True},
            {"name": "last_call", "at_minute": 55, "enabled": True, "dj_mode_ok": False},
        ],
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
    "moderation": {
        "blocklist": [],
    },
    "streams": {
        "youtube": "https://www.youtube.com/@theweathermandj/live",
        "x": "https://x.com/charlesclottin",
        "facebook": "https://www.facebook.com/Charles.clottin",
        "tiktok": "https://www.tiktok.com/@charlesclottin/live",
        "rumble": "https://rumble.com/c/charlesclottin",
    },
    "community": {
        "name": "",
        "in_jokes": [],
        "retention_days": 30,
        "push_bans": False,
        "adapters": {
            "youtube": {"enabled": False, "channel": ""},
            "twitch": {"enabled": False, "channel": ""},
            "kick": {
                "enabled": False,
                "channel": "",
                "access_token": "",
                "broadcaster_user_id": None,
            },
            "facebook": {
                "enabled": False,
                "live_video_id": "",
                "channel": "",
                "page_access_token": "",
                "poll_interval_sec": 3,
            },
            "rumble": {
                "enabled": False,
                "api_url": "",
                "channel": "",
                "poll_interval_sec": 3,
            },
            "youtube_official": {
                "enabled": False,
                "channel": "",
                "live_chat_id": "",
                "api_key": "",
                "daily_quota": 10000,
            },
            "tiktok": {
                "enabled": False,
                "username": "",
                "channel": "",
                "experimental": True,
            },
            "x": {
                "enabled": False,
                "post_id": "",
                "channel": "",
                "bearer_token": "",
                "can_post": False,
                "poll_interval_sec": 15,
                "experimental": True,
            },
        },
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


_SECRET_ADAPTER_KEYS = (
    "access_token",
    "page_access_token",
    "api_key",
    "api_url",  # Rumble URL embeds livestream key
    "bearer_token",  # X API
)


def public_settings(data: dict | None = None) -> dict:
    data = copy.deepcopy(data if data is not None else load())
    key = data.get("halo", {}).get("elevenlabs_key") or ""
    data["halo"]["elevenlabs_key_set"] = bool(key)
    data["halo"]["elevenlabs_key"] = ""
    adapters = ((data.get("community") or {}).get("adapters") or {})
    if isinstance(adapters, dict):
        for _name, acfg in adapters.items():
            if not isinstance(acfg, dict):
                continue
            for sk in _SECRET_ADAPTER_KEYS:
                if sk in acfg:
                    present = bool(acfg.get(sk))
                    acfg[f"{sk}_set"] = present
                    acfg[sk] = ""
    return data
