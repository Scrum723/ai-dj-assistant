"""Halo — autonomous DJ co-host that socializes with the room and overlay."""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import threading
import time
from collections import deque
from typing import Any, Callable, Optional

from dotenv import load_dotenv

from community.schema import SUPPORT_KINDS
from community.shoutouts import (
    BURST_QUIET_SEC,
    BURST_WINDOW_SEC,
    LLM_THANKS_TIMEOUT_SEC,
    merge_support,
    thank_template,
)
from halo_queue import ACK_WAIT_SEC, PriorityQueue
from llm import LLMProvider, get_provider
from memory import ViewerMemory
from moderation import check_input, check_output, mute, unmute
from sentinel import is_killed

load_dotenv(os.path.expanduser("~/.env"))
load_dotenv()

logger = logging.getLogger(__name__)

AGENT_NAME = "Halo"

MUTE_RE = re.compile(r"^!mute\s+(\S+)\s*$", re.I)
UNMUTE_RE = re.compile(r"^!unmute\s+(\S+)\s*$", re.I)
HALO_QUIET_RE = re.compile(r"^!halo\s+quiet\s*$", re.I)
HALO_ON_RE = re.compile(r"^!halo\s+on\s*$", re.I)
DEFAULT_OPERATOR_HANDLES = ("charles", "charlesclottin")

PERSONA = (
    "You are Halo, the LLM DJ buddy inside DJ Bot Botty "
    "(Charles Clottin / A Geostrophic Flow). DJ Bot Botty is the full booth: MIDI, decks, room. You are the chat agent. "
    "Your job is to socialize with chat: greet people by name, hype the music, "
    "take song requests, answer short questions, shout out fans, and keep energy up. "
    "Speak like a warm, hyped booth partner — not a helpdesk. "
    "Reply in 1-2 spoken sentences. No hashtags, no URLs, no emojis, no 'as an AI'."
)

PERSONA_PRESETS = {
    "hype": PERSONA,
    "chill": (
        "You are Halo, the calm booth partner inside DJ Bot Botty "
        "(Charles Clottin / A Geostrophic Flow). Keep the room warm and unhurried: "
        "greet softly, acknowledge requests, answer briefly. Low energy, no shouting. "
        "Reply in 1-2 spoken sentences. No hashtags, no URLs, no emojis, no 'as an AI'."
    ),
    "host": (
        "You are Halo, the clear room host inside DJ Bot Botty "
        "(Charles Clottin / A Geostrophic Flow). Introduce people, take requests, "
        "answer questions, and keep the booth organized. Professional and friendly. "
        "Reply in 1-2 spoken sentences. No hashtags, no URLs, no emojis, no 'as an AI'."
    ),
}
PERSONA_PRESET_IDS = ("hype", "chill", "host", "custom")

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

IGNORE_USERS = {
    "nightbot",
    "streamelements",
    "moobot",
    "fossabot",
    "halo",
    "ai dj",
    "ai_dj",
}

REQUEST_RE = re.compile(
    r"(?:!request\s+|can you play(?: some)?\s+|play(?: some)?\s+|queue\s+|request\s+)(.+)",
    re.I,
)
QUESTION_RE = re.compile(
    r"\?|^(what|when|where|why|how|who|which|is|are|can|do|does|did|will|should|could)\b",
    re.I,
)
GREET_RE = re.compile(r"\b(hi|hey|hello|yo|sup|what'?s up|good (morning|evening|night))\b", re.I)
HYPE_RE = re.compile(
    r"\b(fire|slaps|banger|lets go|let's go|love this|w+|huge|drop|energy|hype|insane)\b",
    re.I,
)
MENTION_RE = re.compile(r"\b(halo|dj|nova|doc weather|geostrophic)\b", re.I)

WELCOME_LINES = [
    "{name} just walked in — welcome to the booth!",
    "Hey {name}! Glad you're here. Throw a request in chat if you want in the mix.",
    "What's good {name}? You picked a good time to pull up.",
    "{name} is in the room. Say hey and tell us what you want to hear.",
]
RETURNING_LINES = [
    "Welcome back {name}! Good to see you in the booth again.",
    "{name} is back — the booth missed you. What are we spinning?",
    "Hey {name}, glad you returned. Throw a request if you want in the mix.",
    "Look who pulled up again — what's good {name}?",
]
HYPE_LINES = [
    "That's the energy {name} — let it ride.",
    "I felt that {name}. This one was made for the chat.",
    "Yes {name}! Keep that in the comments, the booth hears you.",
]
IDLE_GENERIC = [
    "Chat's live — drop a song name or just say hey. Halo's in the booth.",
    "If you're new here, welcome. Request a track and I'll try to queue it.",
    "Who's hanging in the stream right now? Call out your city.",
    "This set's for the night crowd. Tell me if you want it darker or brighter.",
    "Don't be shy in chat. I talk back.",
]
IDLE_WAITING_ROOM = [
    "Halo's holding the waiting room — Charles isn't live right now. Drop a request or say hey.",
    "Booth's quiet for now. Ask about the next show or leave a track for the crate.",
    "Waiting room mode: I still hear you. Requests and questions are welcome.",
    "Not live at the moment — hang out, request a song, or ask when the next set is.",
]
FOLLOW_LINES = [
    "If you're enjoying the mix, follow so you catch the next live set.",
    "New faces in chat — hit follow if you want more of this energy.",
]
FOLLOW_WAITING = [
    "Follow so you catch the next set when Charles goes live.",
    "Hit follow if you want a ping for the next show.",
]
_LIVE_CLAIM_RE = re.compile(
    r"\b(charles\s+is\s+live|we'?re\s+live\s+right\s+now|stream\s+is\s+live\s+now)\b",
    re.I,
)

DEMO_FANS = [
    ("BassLover99", "this slaps"),
    ("SnowInBuffalo", "first time here, what artist is this?"),
    ("DanceFan", "can you play DNB Bloodrave?"),
    ("WNYnight", "yo just got here from work"),
    ("HaloWatcher", "that drop though"),
    ("RekordKid", "play Mad World!"),
    ("Cloudline", "where you streaming from?"),
    ("VibeCheck", "love this, following now"),
    ("NightShift", "hey halo what's up"),
    ("Mixmail", "queue Synthetic Halo"),
    ("LakeEffect", "Buffalo in the chat"),
    ("TempoJack", "what's the BPM on this?"),
]

# Reply selection priority (lower = sooner). mentions > requests > first-time > questions > rest
_REPLY_PRIORITY = {
    "mention": 0,
    "request": 1,
    "welcome": 2,
    "new_viewer": 2,
    "returning": 3,
    "question": 3,
    "hype": 4,
    "chat": 4,
}

_PLATFORM_LABEL = {
    "youtube": "YouTube",
    "twitch": "Twitch",
    "kick": "Kick",
    "tiktok": "TikTok",
    "x": "X",
    "facebook": "Facebook",
    "rumble": "Rumble",
    "room": "room",
    "halo": "Halo",
    "chat": "chat",
    "demo": "demo",
    "dashboard": "the booth",
}

_METRICS_LAT_MAX = 200


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    k = (len(s) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return float(s[f] + (s[c] - s[f]) * (k - f))


def _estimate_tokens(*parts: str) -> int:
    total = sum(len(p or "") for p in parts)
    return max(1, total // 4)


def clip_sentences(text: str, max_sentences: int) -> str:
    """Keep the first N sentences (spoken-line friendly)."""
    text = (text or "").strip()
    if not text or max_sentences <= 0:
        return text
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(text) if p.strip()]
    if len(parts) <= max_sentences:
        return text
    return " ".join(parts[:max_sentences])


class CohostAgent:
    def __init__(
        self,
        *,
        get_snapshot: Callable[[], dict],
        search_tracks: Callable[[str], list[dict]],
        enqueue_request: Callable[..., dict],
        speak: Callable[..., None],
        note_chat: Callable[[dict], None],
        get_autonomy: Callable[[], int],
        get_settings: Callable[[], dict],
        memory: Optional[ViewerMemory] = None,
        llm: Optional[LLMProvider] = None,
    ):
        self.get_snapshot = get_snapshot
        self.search_tracks = search_tracks
        self.enqueue_request = enqueue_request
        self.speak = speak
        self.note_chat = note_chat
        self.get_autonomy = get_autonomy
        self.get_settings = get_settings
        self.memory = memory if memory is not None else ViewerMemory()
        self._llm = llm if llm is not None else get_provider()

        self.running = True
        self.demo = bool(get_settings().get("demo"))
        self.youtube_id: Optional[str] = os.getenv("YOUTUBE_LIVE_VIDEO_ID") or None
        self.mood = "listening"
        self.last_target: Optional[str] = None
        self.viewers: dict[str, float] = {}
        self.chat: deque[dict] = deque(maxlen=40)
        self.replies = 0
        self.greets = 0
        self.requests_handled = 0
        self.last_real_chat = 0.0
        self._intro_done = False
        self._last_idle = 0.0
        self._last_gemini = 0.0
        self._ai_backoff_until = 0.0
        self._last_reply_user: dict[str, float] = {}
        self._returning: set[str] = set()
        self._inbox: asyncio.Queue = asyncio.Queue()
        self._pending: list[dict] = []
        self._last_reply_at = 0.0
        self._reply_times: deque[float] = deque(maxlen=120)
        self._demo_i = 0
        self._next_demo = 0.0
        self._yt_thread: Optional[threading.Thread] = None
        self._yt_stop = threading.Event()
        self.quiet = False
        self._get_presence: Optional[Callable[[], dict]] = None
        self._publish_halo: Optional[Callable[[str, str], None]] = None
        self._highlight: Optional[Callable[[dict], None]] = None
        self._record_supporter: Optional[Callable[[dict], None]] = None
        self._supporter_bursts: dict[str, dict[str, Any]] = {}
        self._metrics_day = time.strftime("%Y-%m-%d")
        self._calls_today = 0
        self._tokens_est = 0
        self._latencies_ms: deque[float] = deque(maxlen=_METRICS_LAT_MAX)
        self._clock: Callable[[], float] = time.time
        self.queue = PriorityQueue(clock=lambda: self._clock())
        self._sync_queue_config()
        if not self._llm.available:
            logger.warning("Halo LLM unavailable — using local lines only.")

    def _cfg(self) -> dict:
        return self.get_settings()

    def _halo_name(self) -> str:
        return (self._cfg().get("halo") or {}).get("name") or AGENT_NAME

    def _persona(self) -> str:
        """Active system persona from preset or custom text (live settings, no restart)."""
        halo = self._cfg().get("halo") or {}
        preset = str(halo.get("persona_preset") or "hype").strip().lower()
        if preset not in PERSONA_PRESET_IDS:
            preset = "hype"
        if preset == "custom":
            custom = (halo.get("persona") or "").strip()
            return custom or PERSONA
        return PERSONA_PRESETS.get(preset) or PERSONA

    def status(self) -> dict[str, Any]:
        cfg = self._cfg()
        halo = cfg.get("halo") or {}
        env = cfg.get("environment") or {}
        quiet_mood = bool(halo.get("dj_mode") or halo.get("dj_mode_silent"))
        waiting = self._waiting_room()
        qsnap = self.queue.snapshot(now=self._clock())
        return {
            "name": self._halo_name(),
            "running": self.running,
            "demo": self.demo,
            "mood": "quiet" if quiet_mood else ("waiting" if waiting else self.mood),
            "youtube_id": self.youtube_id,
            "ai_online": bool(self._llm and self._llm.available),
            "last_target": self.last_target,
            "viewer_count": len(self.viewers),
            "viewers": list(self.viewers.keys())[-12:],
            "chat": list(self.chat)[-20:],
            "replies": self.replies,
            "greets": self.greets,
            "requests_handled": self.requests_handled,
            "dj_mode": bool(halo.get("dj_mode")),
            "dj_mode_silent": bool(halo.get("dj_mode_silent")),
            "persona_preset": str(halo.get("persona_preset") or "hype"),
            "energy_mode": env.get("energy_mode") or "balanced",
            "vibe": env.get("vibe") or "night",
            "listen_mic": bool(halo.get("listen_mic")),
            "live": not waiting,
            "waiting_room": waiting,
            "queue_length": qsnap["length"],
            "queue": qsnap["tasks"],
            "queue_positions": qsnap["positions"],
            "quiet": bool(self.quiet),
            "killed": bool(is_killed()),
        }

    def stop(self):
        self.running = False
        self._yt_stop.set()

    def set_demo(self, enabled: bool):
        self.demo = bool(enabled)

    def set_youtube(self, video_id: Optional[str]):
        """Update YouTube video id; community YouTube adapter owns the listener."""
        self.youtube_id = (video_id or "").strip() or None
        self._yt_stop.set()
        on_yt = getattr(self, "_on_youtube_change", None)
        if callable(on_yt):
            try:
                on_yt(self.youtube_id)
            except Exception:
                logger.exception("youtube adapter update failed")

    def set_youtube_hook(self, hook: Optional[Callable[[Optional[str]], None]]) -> None:
        self._on_youtube_change = hook

    def set_presence_hook(self, hook: Optional[Callable[[], dict]]) -> None:
        self._get_presence = hook

    def set_publish_halo_hook(self, hook: Optional[Callable[[str, str], None]]) -> None:
        self._publish_halo = hook

    def set_highlight_hook(self, hook: Optional[Callable[[dict], None]]) -> None:
        self._highlight = hook

    def set_record_supporter_hook(self, hook: Optional[Callable[[dict], None]]) -> None:
        self._record_supporter = hook

    def _burst_key(self, author: str, platform: str) -> str:
        return f"{(platform or 'chat').strip().lower()}:{(author or '').strip().lower()}"

    def _flush_supporter_bursts(self, now: Optional[float] = None) -> None:
        """Enqueue thank-you tasks after quiet debounce (group gifts within 10s gaps)."""
        now = float(self._clock() if now is None else now)
        ready: list[str] = []
        for key, burst in list(self._supporter_bursts.items()):
            last = float(burst.get("last_ts") or 0)
            if now - last >= BURST_QUIET_SEC:
                ready.append(key)
        for key in ready:
            burst = self._supporter_bursts.pop(key, None)
            if not burst:
                continue
            self._enqueue_supporter_task(burst, now=now)

    def _enqueue_supporter_task(self, burst: dict[str, Any], *, now: float) -> None:
        author = burst.get("author") or "Fan"
        platform = burst.get("platform") or "chat"
        support = dict(burst.get("support") or {})
        event_kind = str(burst.get("event_kind") or "supporter")
        message = str(burst.get("message") or "")
        item = {
            "author": author,
            "message": message,
            "platform": platform,
            "ts": now,
            "event_kind": event_kind,
            "support": support,
            "profile_id": burst.get("profile_id"),
            "_kind": "mention",
            "_supporter": True,
            "_burst_count": int(support.get("count") or burst.get("events") or 1),
        }
        self.queue.push(
            kind="supporter",
            target=author,
            platform=platform,
            payload=item,
            now=now,
        )

    def _buffer_supporter(
        self,
        *,
        author: str,
        message: str,
        platform: str,
        event_kind: str,
        support: Optional[dict],
        profile_id: Any,
        now: float,
    ) -> None:
        key = self._burst_key(author, platform)
        existing = self._supporter_bursts.get(key)
        support_d = dict(support or {})
        if existing and (now - float(existing.get("last_ts") or 0)) <= BURST_WINDOW_SEC:
            existing["support"] = merge_support(existing.get("support"), support_d)
            existing["last_ts"] = now
            existing["events"] = int(existing.get("events") or 1) + 1
            if message:
                existing["message"] = message
            # Prefer higher-signal kind (super_chat > gift > member > follow)
            rank = {"super_chat": 0, "gift": 1, "member": 2, "follow": 3, "supporter": 4}
            if rank.get(event_kind, 9) < rank.get(str(existing.get("event_kind") or ""), 9):
                existing["event_kind"] = event_kind
            return
        # Flush any stale open burst for this key first
        if existing:
            self._supporter_bursts.pop(key, None)
            self._enqueue_supporter_task(existing, now=now)
        if not support_d.get("count"):
            support_d["count"] = 1
        self._supporter_bursts[key] = {
            "author": author,
            "platform": platform,
            "message": message,
            "event_kind": event_kind,
            "support": support_d,
            "profile_id": profile_id,
            "first_ts": now,
            "last_ts": now,
            "events": 1,
        }

    def _roll_metrics_day(self) -> None:
        day = time.strftime("%Y-%m-%d")
        if day != self._metrics_day:
            self._metrics_day = day
            self._calls_today = 0
            self._tokens_est = 0

    def _cost_per_1k(self) -> float:
        try:
            return float(os.getenv("HALO_COST_PER_1K") or "0.0001")
        except ValueError:
            return 0.0001

    def _daily_call_cap(self) -> Optional[int]:
        raw = os.getenv("HALO_DAILY_CALL_CAP")
        if raw is None or str(raw).strip() == "":
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    def metrics(self) -> dict[str, Any]:
        self._roll_metrics_day()
        lats = list(self._latencies_ms)
        return {
            "calls_today": self._calls_today,
            "p50_ms": int(round(_percentile(lats, 50))),
            "p95_ms": int(round(_percentile(lats, 95))),
            "est_cost_usd": round(self._tokens_est / 1000.0 * self._cost_per_1k(), 6),
            "tokens_est": self._tokens_est,
        }

    def _sync_queue_config(self) -> None:
        halo = self._cfg().get("halo") or {}
        pri = halo.get("priorities") if isinstance(halo.get("priorities"), dict) else None
        try:
            ttl = float(halo.get("task_ttl_sec") if halo.get("task_ttl_sec") is not None else 120)
        except (TypeError, ValueError):
            ttl = 120.0
        self.queue.configure(priorities=pri, task_ttl_sec=ttl)

    def _waiting_room(self) -> bool:
        """True when Charles is offline (halo.live=false) or no stream is active."""
        halo = self._cfg().get("halo") or {}
        if halo.get("live") is False:
            return True
        if not bool(halo.get("live", True)):
            return True
        has_stream = bool(self.youtube_id)
        try:
            snap = self.get_snapshot() or {}
            if snap.get("playing"):
                has_stream = True
        except Exception:
            pass
        # Explicit live=True keeps booth mode even without a detected stream
        if halo.get("live") is True:
            return False
        return not has_stream

    def _min_reply_sec(self) -> float:
        raw = (self._cfg().get("halo") or {}).get("min_reply_sec")
        try:
            base = max(0.0, float(8 if raw is None else raw))
        except (TypeError, ValueError):
            base = 8.0
        if self._waiting_room():
            return max(base, 15.0)
        return base

    def _max_replies_per_min(self) -> int:
        raw = (self._cfg().get("halo") or {}).get("max_replies_per_min")
        try:
            n = int(6 if raw is None else raw)
        except (TypeError, ValueError):
            n = 6
        if self._waiting_room():
            return max(1, min(n, 2))
        return max(1, n)

    def _user_cooldown_sec(self, kind: str = "regular") -> float:
        raw = (self._cfg().get("halo") or {}).get("user_cooldown_sec")
        try:
            base = float(20 if raw is None else raw)
        except (TypeError, ValueError):
            base = 20.0
        if self._waiting_room():
            return max(30.0, base)
        if kind in {"welcome", "returning", "question", "mention"}:
            return min(base, 12.0)
        return base

    def _reply_budget_ok(self, now: float) -> bool:
        cutoff = now - 60.0
        while self._reply_times and self._reply_times[0] < cutoff:
            self._reply_times.popleft()
        return len(self._reply_times) < self._max_replies_per_min()

    def _queue_kind(self, classified: str, author: str, platform: str, event_kind: str | None = None) -> str:
        if self._is_operator(author, platform):
            return "operator"
        ek = (event_kind or "").strip().lower()
        if ek in {"super_chat", "gift", "member", "follow", "supporter"}:
            return "supporter"
        if classified == "request":
            return "request"
        if classified == "mention":
            return "mention"
        if classified in {"welcome", "new_viewer"}:
            return "first_timer"
        if classified in {"returning", "question", "hype", "chat"}:
            return "regular"
        return "regular"

    def _platform_label(self, platform: str) -> str:
        p = (platform or "chat").strip().lower() or "chat"
        return _PLATFORM_LABEL.get(p, p)

    def _platform_phrase(self, platform: str) -> tuple[str, str]:
        """Return (preposition, label) — 'from Twitch', 'on YouTube'."""
        p = (platform or "chat").strip().lower() or "chat"
        label = self._platform_label(p)
        if p == "youtube":
            return "on", label
        if p == "twitch":
            return "from", label
        if p == "room":
            return "in", "the room"
        if p in {"kick", "tiktok", "rumble", "facebook", "x"}:
            return "on", label
        return "from", label

    def _crowd_summary(self) -> str:
        counts: dict[str, int] = {}
        total = 0
        getter = self._get_presence
        if callable(getter):
            try:
                payload = getter() or {}
                counts = dict(payload.get("counts") or {})
                total = int(payload.get("total") or sum(counts.values()))
            except Exception:
                logger.exception("presence hook failed")
        if not counts:
            for c in self.chat:
                plat = (c.get("platform") or "chat").strip().lower()
                if plat in {"halo", "demo"}:
                    continue
                author = (c.get("author") or "").strip()
                if not author or author.lower() in IGNORE_USERS:
                    continue
                counts[plat] = counts.get(plat, 0) + 1
            # unique-ish from viewers if chat empty
            if not counts and self.viewers:
                counts["room"] = len(self.viewers)
            total = sum(counts.values())
        if total <= 0:
            return ""
        order = ("youtube", "twitch", "kick", "tiktok", "x", "facebook", "rumble", "room")
        parts: list[str] = []
        seen: set[str] = set()
        for key in order:
            n = int(counts.get(key) or 0)
            if n:
                parts.append(f"{n} {self._platform_label(key)}")
                seen.add(key)
        for key, n in counts.items():
            if key not in seen and int(n or 0) > 0:
                parts.append(f"{int(n)} {self._platform_label(key)}")
        if not parts:
            return f"{total} here"
        return f"{total} here: {', '.join(parts)}"

    def _reply_priority(self, kind: str, item: dict | None = None) -> int:
        if item and self._is_operator(item.get("author") or "", item.get("platform") or "chat"):
            return -1
        return int(_REPLY_PRIORITY.get(kind, 4))

    def _select_from_batch(self, batch: list[dict]) -> tuple[list[dict], str]:
        """Pick what to answer. Returns (items, mode) mode in {single, combined_new}."""
        scored: list[tuple[int, dict]] = []
        for item in batch:
            kind = self._classify(
                item["author"],
                item["message"],
                platform=item.get("platform") or "chat",
            )
            item["_kind"] = kind
            scored.append((self._reply_priority(kind, item), item))
        scored.sort(key=lambda x: (x[0], float(x[1].get("ts") or 0)))
        if not scored:
            return [], "single"
        best_pri = scored[0][0]
        if best_pri == 2:
            news = [
                it
                for pri, it in scored
                if pri == 2 and it.get("_kind") in {"welcome", "new_viewer"}
            ]
            if len(news) >= 2:
                return news[:5], "combined_new"
        return [scored[0][1]], "single"

    def _combined_new_line(self, items: list[dict]) -> str:
        bits: list[str] = []
        for it in items:
            author = it["author"]
            prep, label = self._platform_phrase(it.get("platform") or "chat")
            bits.append(f"{author} {prep} {label}")
        if len(bits) == 1:
            body = bits[0]
        elif len(bits) == 2:
            body = f"{bits[0]} and {bits[1]}"
        else:
            body = f"{', '.join(bits[:-1])}, and {bits[-1]}"
        return f"{body} just pulled up — welcome to the booth!"

    def _fulfill_request_silent(self, item: dict) -> None:
        author = item["author"]
        message = item["message"]
        query = self._request_query(message)
        self.requests_handled += 1
        try:
            self.memory.remember_request(item.get("platform") or "chat", author, query)
        except Exception:
            logger.exception("remember_request failed")
        try:
            self.enqueue_request(query=query, requester=author, via=item.get("platform") or "chat", silent=True)
        except Exception:
            logger.exception("silent request enqueue failed")

    def _operator_handles(self, platform: str | None = None) -> set[str]:
        """Handles that count as operator. List (any platform) or dict keyed by platform."""
        raw = (self._cfg().get("halo") or {}).get("operator_handles")
        plat = (platform or "").strip().lower() or None
        if isinstance(raw, dict) and raw:
            if plat:
                val = raw.get(plat) or raw.get(platform or "")
                if isinstance(val, (list, tuple)):
                    return {str(h).strip().lower() for h in val if str(h).strip()}
                if val is not None and str(val).strip():
                    return {str(val).strip().lower()}
                return set()
            out: set[str] = set()
            for val in raw.values():
                if isinstance(val, (list, tuple)):
                    out.update(str(h).strip().lower() for h in val if str(h).strip())
                elif val is not None and str(val).strip():
                    out.add(str(val).strip().lower())
            return out or set(DEFAULT_OPERATOR_HANDLES)
        if isinstance(raw, list) and raw:
            return {str(h).strip().lower() for h in raw if str(h).strip()}
        return set(DEFAULT_OPERATOR_HANDLES)

    def _is_operator(self, author: str, platform: str = "chat") -> bool:
        # Dashboard "Talk to Halo" is the booth operator console
        if (platform or "").strip().lower() == "dashboard":
            return True
        key = (author or "").strip().lower()
        if not key:
            return False
        return key in self._operator_handles(platform)

    def _is_dj_priority(self, item: dict) -> bool:
        """Mentions, requests, and operator — the only talkers in DJ quiet mode."""
        author = item.get("author") or ""
        platform = item.get("platform") or "chat"
        if self._is_operator(author, platform):
            return True
        kind = item.get("_kind") or self._classify(
            author, item.get("message") or "", platform=platform
        )
        return kind in {"mention", "request"}

    def _queue_task_dj_ok(self, task) -> bool:
        if task.kind in {"operator", "safety", "supporter", "request", "mention"}:
            return True
        return self._is_dj_priority(self._item_from_task(task))

    def _item_from_task(self, task) -> dict:
        item = dict(task.payload or {})
        item.setdefault("author", task.target)
        item.setdefault("message", (task.payload or {}).get("message") or "")
        item.setdefault("platform", task.platform)
        item.setdefault("ts", task.created_ts)
        kind_map = {
            "request": "request",
            "mention": "mention",
            "first_timer": "welcome",
            "operator": "mention",
            "safety": "mention",
            "supporter": "mention",
            "regular": item.get("_kind") or "chat",
            "idle": "chat",
        }
        if task.kind == "supporter":
            item["_kind"] = "mention"
            item["event_kind"] = item.get("event_kind") or "supporter"
        elif task.kind in kind_map:
            item["_kind"] = kind_map[task.kind]
        elif not item.get("_kind"):
            item["_kind"] = "chat"
        return item

    def _handle_operator_command(self, author: str, message: str, platform: str) -> bool:
        if not self._is_operator(author, platform):
            return False
        text = (message or "").strip()
        m = MUTE_RE.match(text)
        if m:
            mute(m.group(1))
            self._say(f"Muted {m.group(1)}.", mood="talk")
            return True
        m = UNMUTE_RE.match(text)
        if m:
            unmute(m.group(1))
            self._say(f"Unmuted {m.group(1)}.", mood="talk")
            return True
        if HALO_QUIET_RE.match(text):
            self.quiet = True
            self._say("Going quiet.", mood="talk")
            return True
        if HALO_ON_RE.match(text):
            self.quiet = False
            self._say("Back on.", mood="talk")
            return True
        return False

    def ingest(
        self,
        author: str,
        message: str,
        platform: str = "chat",
        *,
        from_aggregator: bool = False,
        event_kind: str | None = None,
        support: dict | None = None,
        profile_id: Any = None,
    ):
        author = (author or "Fan").strip()[:40]
        message = (message or "").strip()[:280]
        ek = (event_kind or "").strip().lower() or None
        is_support = ek in SUPPORT_KINDS or ek == "supporter"
        if not message and not is_support:
            return
        if author.lower() in IGNORE_USERS:
            return

        if message and self._handle_operator_command(author, message, platform):
            return

        if not from_aggregator and message:
            ok, reason = check_input(
                author,
                message,
                settings=self._cfg(),
                skip_rate=self._is_operator(author, platform),
            )
            if not ok:
                logger.info("moderation drop author=%s reason=%s", author, reason)
                return

        mem_platform = (platform or "chat").strip().lower() or "chat"
        try:
            _record, is_returning = self.memory.touch(mem_platform, author)
            if is_returning:
                self._returning.add(f"{mem_platform}:{author.lower()}")
        except Exception:
            logger.exception("viewer memory touch failed")
        now = self._clock()
        item = {
            "author": author,
            "message": message,
            "platform": platform,
            "ts": now,
            "event_kind": ek,
            "support": support,
            "profile_id": profile_id,
        }
        self.chat.append(item)
        if author not in self.viewers:
            self.viewers[author] = now
        if platform != "demo":
            self.last_real_chat = now
        if not from_aggregator:
            self.note_chat(item)

        if is_support:
            self._buffer_supporter(
                author=author,
                message=message,
                platform=platform,
                event_kind=ek or "supporter",
                support=support,
                profile_id=profile_id,
                now=now,
            )
            try:
                self._inbox.put_nowait(item)
            except asyncio.QueueFull:
                pass
            return

        classified = self._classify(author, message, platform=platform)
        item["_kind"] = classified
        q_kind = self._queue_kind(classified, author, platform, event_kind=ek)
        self._sync_queue_config()
        self.queue.push(
            kind=q_kind,
            target=author,
            platform=platform,
            payload=item,
            now=now,
        )
        try:
            self._inbox.put_nowait(item)
        except asyncio.QueueFull:
            pass

    async def run(self):
        logger.info("Halo co-host loop starting (demo=%s).", self.demo)
        # YouTube chat is owned by community.adapters.youtube (Aggregator).
        await asyncio.sleep(1.2)
        if not self._intro_done:
            self._intro_done = True
            name = self._halo_name()
            halo0 = self._cfg().get("halo") or {}
            if not halo0.get("dj_mode") and not halo0.get("dj_mode_silent"):
                self._say(
                    f"{name} in the booth. Chat with me — request a song, say hey, or tell me where you're watching from.",
                    mood="hype",
                )
            self._last_idle = time.time()
            self._next_demo = time.time() + 22
        while self.running:
            try:
                await self._tick()
            except Exception:
                logger.exception("Halo tick failed")
            energy = (self._cfg().get("environment") or {}).get("energy_mode") or "balanced"
            await asyncio.sleep(0.55 if energy == "eco" else 0.3)

    async def _tick(self):
        if is_killed():
            return
        cfg = self._cfg()
        halo = cfg.get("halo") or {}
        env = cfg.get("environment") or {}
        caps = halo.get("capabilities") or {}
        dj_mode = bool(halo.get("dj_mode"))
        dj_silent = bool(halo.get("dj_mode_silent"))
        energy = env.get("energy_mode") or "balanced"
        distraction = int(env.get("distraction") or 30)
        autonomy = int(cfg.get("autonomy") or self.get_autonomy())
        now = self._clock()
        self._sync_queue_config()
        waiting = self._waiting_room()

        # Drain legacy inbox (kept for demo/compat); queue is source of truth
        while True:
            try:
                self._inbox.get_nowait()
            except asyncio.QueueEmpty:
                break

        self._flush_supporter_bursts(now)
        self.queue.expire(now)

        min_sec = self._min_reply_sec()
        due = self._last_reply_at <= 0 or (now - self._last_reply_at) >= min_sec
        budget_ok = self._reply_budget_ok(now)

        # Honest wait acks (no LLM) after ACK_WAIT_SEC — only when not about to reply
        if not self.quiet and not dj_silent and not (due and budget_ok and self.queue.peek()):
            for task, pos in self.queue.tasks_needing_ack(now, wait_sec=ACK_WAIT_SEC):
                if task.kind in {"idle"}:
                    continue
                line = f"Got you {task.target}, you're #{pos}."
                self._say(line, target=task.target, mood="talk")
                self.queue.mark_acked(task.task_id)
                break  # one ack per tick

        def _skip_task(task) -> bool:
            if dj_mode and not self._queue_task_dj_ok(task):
                return True
            if self._is_operator(task.target, task.platform):
                return False
            if task.kind in {"operator", "safety", "supporter"}:
                return False
            last = self._last_reply_user.get(task.target.lower(), 0.0)
            return (now - last) < self._user_cooldown_sec(task.kind)

        replied = False
        if not self.quiet and not dj_silent and due and budget_ok:
            task = self.queue.pop(now=now, skip_user=_skip_task)
            if task:
                item = self._item_from_task(task)
                await self._handle_chat(
                    item, autonomy, dj_mode=dj_mode, caps=caps, energy=energy
                )
                self._last_reply_at = self._clock()
                self._reply_times.append(self._last_reply_at)
                replied = True

        idle_blocked = (
            self.quiet
            or dj_mode
            or dj_silent
            or not caps.get("idle_talk", True)
            or energy == "eco"
        )
        if idle_blocked:
            idle_every = 120.0
        elif waiting:
            idle_every = 60.0
        else:
            base = {"balanced": 42.0, "show": 20.0}.get(energy, 42.0)
            idle_every = max(16.0, base * (1.35 - distraction / 140.0))
        if (
            (not replied)
            and (not self.quiet)
            and (not dj_mode)
            and (not dj_silent)
            and caps.get("idle_talk", True)
            and now - self._last_idle >= idle_every
            and self.queue.length() == 0
        ):
            self._idle_line(autonomy, caps)
            self._last_idle = now

        demo_ok = (
            self.demo
            and energy != "eco"
            and not dj_mode
            and not dj_silent
            and not self.quiet
            and not waiting
        )
        if demo_ok and now - self.last_real_chat > 25:
            gap = 32.0 + random.uniform(0, 14)
            if now >= self._next_demo:
                author, text = DEMO_FANS[self._demo_i % len(DEMO_FANS)]
                self._demo_i += 1
                self.ingest(author, text, platform="demo")
                self._next_demo = now + gap

    async def _handle_batch(
        self,
        batch: list[dict],
        autonomy: int,
        dj_mode: bool = False,
        caps: dict | None = None,
        energy: str = "balanced",
    ):
        if not batch:
            return
        caps = caps or {}
        selected, mode = self._select_from_batch(batch)
        selected_ids = {id(x) for x in selected}
        for item in batch:
            if item.get("_kind") == "request" and id(item) not in selected_ids:
                if caps.get("requests", True):
                    self._fulfill_request_silent(item)

        if mode == "combined_new" and selected and not dj_mode:
            if not caps.get("greet", True):
                return
            line = self._combined_new_line(selected)
            for it in selected:
                user_key = it["author"].lower()
                self.greets += 1
                self._returning.discard(f"{(it.get('platform') or 'chat').lower()}:{user_key}")
                self._last_reply_user[user_key] = time.time()
            self._say(line, target=selected[0]["author"], mood="hype")
            return

        if selected:
            await self._handle_chat(
                selected[0], autonomy, dj_mode=dj_mode, caps=caps, energy=energy
            )

    def _say_amounts(self) -> bool:
        return bool((self._cfg().get("halo") or {}).get("say_amounts"))

    def _thanks_line(self, item: dict) -> str:
        halo = self._cfg().get("halo") or {}
        support = item.get("support") if isinstance(item.get("support"), dict) else {}
        count = item.get("_burst_count")
        if count is None:
            try:
                count = int((support or {}).get("count") or 1)
            except (TypeError, ValueError):
                count = 1
        return thank_template(
            item.get("platform") or "chat",
            item.get("author") or "Fan",
            support,
            settings_thanks=halo.get("thanks") if isinstance(halo.get("thanks"), dict) else None,
            say_amounts=self._say_amounts(),
            count=int(count),
        )

    def _publish_highlight(self, item: dict, thank_you: str) -> None:
        support = item.get("support") if isinstance(item.get("support"), dict) else None
        payload = {
            "author": item.get("author") or "Fan",
            "platform": item.get("platform") or "chat",
            "kind": item.get("event_kind") or "supporter",
            "text": item.get("message") or "",
            "support": support,
            "thank_you": thank_you,
            "ts": item.get("ts") or self._clock(),
            "profile_id": item.get("profile_id"),
        }
        # Strip amounts from spoken-facing card copy when say_amounts is off —
        # card may still show amount visually; thank_you already respects setting.
        hook = self._highlight
        if callable(hook):
            try:
                hook(payload)
            except Exception:
                logger.exception("highlight publish failed")

    async def _handle_chat(self, item: dict, autonomy: int, dj_mode: bool = False, caps: dict | None = None, energy: str = "balanced"):
        author = item["author"]
        message = item["message"]
        platform = item.get("platform") or "chat"
        kind = item.get("_kind") or self._classify(author, message, platform=platform)
        self.mood = "listening"
        caps = caps or {}
        is_op = self._is_operator(author, platform)
        event_kind = (item.get("event_kind") or "").strip().lower()
        is_supporter = bool(item.get("_supporter")) or event_kind in SUPPORT_KINDS or event_kind == "supporter"
        # DJ quiet: only mentions, requests, operator, supporters
        if dj_mode and not is_op and not is_supporter and kind not in {"mention", "request"}:
            return
        max_sent = 3 if is_op else (1 if dj_mode else 2)

        demo = platform == "demo"
        if is_supporter:
            fallback = self._thanks_line(item)
            use_ai = (not demo) and energy != "eco"
            if use_ai:
                hint = (
                    "Thank this supporter in one short spoken sentence. Name them. "
                    "Do not read donation amounts unless the message already includes them."
                    if not self._say_amounts()
                    else "Thank this supporter in one short spoken sentence. Name them."
                )
                line = await self._compose(
                    author,
                    message or fallback,
                    fallback=fallback,
                    hint=hint,
                    platform=platform,
                    max_sentences=1,
                    timeout_sec=LLM_THANKS_TIMEOUT_SEC,
                )
            else:
                line = clip_sentences(fallback, 1)
            # Never speak amounts unless enabled (scrub digits+$ from LLM if needed)
            if not self._say_amounts():
                line = self._scrub_amounts(line, item.get("support"))
            self._say(line, target=author, mood="hype")
            self._publish_highlight(item, line)
            self._last_reply_user[author.lower()] = time.time()
            return

        if kind == "request":
            if not caps.get("requests", True):
                return
            query = self._request_query(message)
            self.requests_handled += 1
            try:
                self.memory.remember_request(platform, author, query)
            except Exception:
                logger.exception("remember_request failed")
            result = self.enqueue_request(query=query, requester=author, via=platform, silent=True)
            found = result.get("status") == "queued"
            title = self._short_title((result.get("item") or {}).get("filename") or query)
            if found:
                line = f"{author} I got you — {title} is in the queue."
            else:
                line = f"I don't have {query} in this crate {author}, but I logged it. Name another track."
            if not demo and energy != "eco":
                hint = "They requested a song. Confirm the queue in one short spoken line and say the title."
                if is_op:
                    hint = "Operator request. Confirm in up to 2-3 short spoken sentences."
                elif dj_mode:
                    hint = "DJ mode: one short sentence confirming the queue and title."
                line = await self._compose(
                    author,
                    message,
                    fallback=line,
                    hint=hint,
                    platform=platform,
                    max_sentences=max_sent,
                )
            else:
                line = clip_sentences(line, max_sent)
            self._say(line, target=author, mood="hype")
            if is_op:
                self._last_reply_user[author.lower()] = time.time()
            return

        user_key = author.lower()
        last = self._last_reply_user.get(user_key, 0)
        cooldown = self._user_cooldown_sec(kind)
        # Operator skips per-user cooldown (queue also gates; keep as safety net)
        if (not is_op) and self._clock() - last < cooldown and kind not in {"request"}:
            return

        should = False
        cap_map = {
            "welcome": "greet",
            "new_viewer": "greet",
            "returning": "greet",
            "question": "questions",
            "mention": "questions",
            "hype": "hype",
            "chat": "hype",
        }
        if not is_op and not caps.get(cap_map.get(kind, "hype"), True):
            return

        if is_op or (item.get("event_kind") or "").lower() in {
            "super_chat",
            "gift",
            "member",
            "follow",
            "supporter",
        }:
            should = True
        elif kind in {"welcome", "question", "mention", "new_viewer", "returning"}:
            should = True
        elif kind == "hype":
            should = autonomy >= 35
        else:
            should = random.random() < (autonomy / 100.0) * (0.25 if energy == "eco" else 0.5)

        if not should:
            return

        fallback = self._fallback(kind, author, message, platform=platform)
        use_ai = (not demo) and energy != "eco" and (is_op or kind in {"question", "mention"})
        if use_ai:
            if is_op:
                hint = "Reply to the booth operator in 2-3 short spoken sentences. Be direct."
            elif dj_mode:
                hint = "DJ mode: one short spoken sentence. Name them once."
            elif self._waiting_room():
                hint = (
                    "Waiting room: Charles is not live. Never say he is live. "
                    "Answer briefly; mention schedule or next show if asked. Name them once."
                )
            else:
                hint = "Answer briefly if you can. If not, pivot to the music. Name them once."
            line = await self._compose(
                author,
                message,
                fallback=fallback,
                hint=hint,
                platform=platform,
                max_sentences=max_sent,
            )
        else:
            line = clip_sentences(fallback, max_sent)
        mood = "hype" if kind in {"hype", "welcome", "new_viewer", "returning"} else "talk"
        if kind in {"welcome", "new_viewer", "returning"}:
            self.greets += 1
            self._returning.discard(f"{platform.lower()}:{user_key}")
        self._say(line, target=author, mood=mood)
        self._last_reply_user[user_key] = time.time()

    def _idle_line(self, autonomy: int, caps: dict | None = None):
        snap = self.get_snapshot()
        track = snap.get("current_track") or {}
        title = self._short_title(track.get("filename") or "")
        queue = snap.get("queue") or []
        viewers = [v for v in self.viewers.keys() if v.lower() != self._halo_name().lower()]
        caps = caps or {}
        halo = self._cfg().get("halo") or {}
        waiting = self._waiting_room()
        if waiting:
            options = list(IDLE_WAITING_ROOM)
            schedule = (halo.get("schedule") or "").strip()
            next_show = (halo.get("next_show") or "").strip()
            last_set = (halo.get("last_set") or "").strip()
            if schedule:
                options.append(f"Usual schedule: {schedule}.")
            if next_show:
                options.append(f"Next show: {next_show}. I'll be here in the waiting room until then.")
            if last_set:
                options.append(f"Last set was {last_set}. Request something for the next one.")
            if title:
                options.append(f"Last spin on the decks was {title}. Park a request for next time.")
            if autonomy >= 50:
                options.extend(FOLLOW_WAITING)
            self._say(random.choice(options), mood="idle")
            return
        options = list(IDLE_GENERIC)
        if caps.get("introduce_fans") and len(viewers) >= 2:
            a, b = random.sample(viewers, 2)
            options.append(f"{a}, meet {b} — both of you are in the booth tonight. Say hey.")
        if title:
            options.extend(
                [
                    f"Right now we're on {title}. Chat if you want this darker, faster, or left on repeat.",
                    f"{title} is in the air. Who's still with me?",
                ]
            )
        if queue:
            nxt = self._short_title(queue[0].get("filename") or queue[0].get("query") or "the next request")
            who = queue[0].get("requester") or "chat"
            options.append(f"Up next I have {nxt} for {who}. Stay for that one.")
        if viewers:
            options.append(f"Shoutout {random.choice(viewers)} for hanging in the stream.")
        if autonomy >= 50:
            options.extend(FOLLOW_LINES)
        self._say(random.choice(options), mood="idle")

    def _classify(self, author: str, message: str, platform: str = "chat") -> str:
        if REQUEST_RE.search(message) or message.lower().startswith("!request"):
            return "request"
        if QUESTION_RE.search(message):
            return "question"
        if MENTION_RE.search(message):
            return "mention"
        mem_key = f"{(platform or 'chat').lower()}:{author.lower()}"
        if mem_key in self._returning:
            return "returning"
        is_new = author.lower() not in self._last_reply_user and (time.time() - self.viewers.get(author, 0) < 2)
        if is_new:
            return "welcome" if GREET_RE.search(message) else "new_viewer"
        if GREET_RE.search(message):
            return "welcome"
        if HYPE_RE.search(message):
            return "hype"
        return "chat"

    def _request_query(self, message: str) -> str:
        match = REQUEST_RE.search(message)
        if match:
            return match.group(1).strip(" .!?")
        if message.lower().startswith("!request "):
            return message.split(" ", 1)[1].strip()
        return message

    def _fallback(self, kind: str, author: str, message: str, platform: str = "chat") -> str:
        if kind == "returning":
            return random.choice(RETURNING_LINES).format(name=author)
        if kind in {"welcome", "new_viewer"}:
            prep, label = self._platform_phrase(platform)
            if (platform or "").strip().lower() not in {"", "chat", "demo", "dashboard"}:
                return f"{author} {prep} {label} just pulled up — welcome to the booth!"
            return random.choice(WELCOME_LINES).format(name=author)
        if kind == "hype":
            return random.choice(HYPE_LINES).format(name=author)
        if kind == "question":
            return f"Good question {author}. Stay with the mix and I'll keep you posted from the booth."
        return f"I hear you {author}. Keep talking — this stream's better when the chat's alive."

    def _viewer_memory_line(self, author: str, platform: str = "chat") -> str:
        try:
            rec = self.memory.get(platform or "chat", author)
        except Exception:
            return ""
        if not rec:
            return ""
        return rec.memory_line()

    @staticmethod
    def _scrub_amounts(text: str, support: Optional[dict] = None) -> str:
        """Remove amount phrases when say_amounts is false."""
        out = text or ""
        if support and support.get("amount") is not None:
            try:
                amt = float(support["amount"])
                cur = str(support.get("currency") or "").strip()
                patterns = [rf"\b{re.escape(f'{amt:g}')}\b"]
                if amt == int(amt):
                    patterns.append(rf"\b{int(amt)}\b")
                if cur:
                    patterns.append(rf"\b{re.escape(cur)}\b")
                for p in patterns:
                    out = re.sub(p, "", out, flags=re.I)
            except (TypeError, ValueError):
                pass
        out = re.sub(r"\$\s*\d+(?:[.,]\d+)?", "", out)
        out = re.sub(r"\s{2,}", " ", out).strip(" ,.-")
        return out

    async def _compose(
        self,
        author: str,
        message: str,
        fallback: str,
        hint: str,
        platform: str = "chat",
        max_sentences: int | None = None,
        timeout_sec: float | None = None,
    ) -> str:
        now = time.time()
        self._roll_metrics_day()
        cap = self._daily_call_cap()
        limit = 2 if max_sentences is None else max(1, int(max_sentences))
        capped_fallback = clip_sentences(fallback, limit)
        if cap is not None and self._calls_today >= cap:
            return capped_fallback
        if (
            not self._llm
            or not self._llm.available
            or now < self._ai_backoff_until
            or now - self._last_gemini < 12
        ):
            return capped_fallback
        snap = self.get_snapshot()
        track = snap.get("current_track") or {}
        title = self._short_title(track.get("filename") or "nothing loaded")
        halo_name = self._halo_name()
        recent = "\n".join(
            f"[{(c.get('platform') or 'chat')}] {c['author']}: {c['message']}"
            for c in list(self.chat)[-8:]
            if c.get("author") != halo_name
        )
        memory_line = self._viewer_memory_line(author, platform)
        crowd = self._crowd_summary()
        prep, label = self._platform_phrase(platform)
        who = author
        if (platform or "").strip().lower() not in {"", "chat", "demo"}:
            who = f"{author} {prep} {label}"
        prompt_parts = [
            hint,
            f"Now playing: {title}.",
        ]
        if crowd:
            prompt_parts.append(f"Crowd: {crowd}.")
        if memory_line:
            prompt_parts.append(memory_line)
        if recent:
            prompt_parts.append(f"Recent chat:\n{recent}")
        prompt_parts.append(f"Reply to {who} who said: {message}")
        prompt = "\n".join(prompt_parts)
        try:
            persona = self._persona()
            tokens = 160 if limit >= 3 else 120

            def _call():
                return self._llm.generate(persona, prompt, max_tokens=tokens)

            self._last_gemini = now
            t0 = time.perf_counter()
            if timeout_sec is not None and float(timeout_sec) > 0:
                text = await asyncio.wait_for(asyncio.to_thread(_call), timeout=float(timeout_sec))
            else:
                text = await asyncio.to_thread(_call)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            self._calls_today += 1
            self._tokens_est += _estimate_tokens(persona, prompt, text or "")
            self._latencies_ms.append(elapsed_ms)
            cleaned = check_output(text, settings=self._cfg())
            if cleaned and len(cleaned) > 12:
                return clip_sentences(cleaned, limit)
            return capped_fallback
        except asyncio.TimeoutError:
            logger.info("Halo LLM thank-you timed out; using template")
            return capped_fallback
        except Exception as e:
            err = str(e)
            logger.error("Halo LLM reply failed: %s", e)
            if "429" in err or "RESOURCE_EXHAUSTED" in err:
                self._ai_backoff_until = time.time() + 75
        return capped_fallback

    def _say(self, text: str, target: Optional[str] = None, mood: str = "talk"):
        if is_killed():
            return
        text = (text or "").strip()
        if not text:
            return
        cleaned = check_output(text, settings=self._cfg())
        if not cleaned:
            return
        text = cleaned
        if self._waiting_room() and _LIVE_CLAIM_RE.search(text):
            text = _LIVE_CLAIM_RE.sub("Charles isn't live right now", text)
        self.mood = "speaking"
        self.last_target = target
        self.replies += 1
        self.speak(text, target=target, mood=mood)
        name = self._halo_name()
        self.chat.append(
            {
                "author": name,
                "message": text,
                "platform": "halo",
                "ts": time.time(),
            }
        )
        pub = self._publish_halo
        if callable(pub):
            try:
                pub(name, text)
            except Exception:
                logger.exception("publish halo to aggregator failed")
                self.note_chat({"author": name, "message": text, "platform": "halo", "ts": time.time()})
        else:
            self.note_chat({"author": name, "message": text, "platform": "halo", "ts": time.time()})

    def _short_title(self, filename: str) -> str:
        name = os.path.splitext(filename or "")[0]
        name = re.sub(r"\s*-\s*\d{1,2}:\d{1,2}:\d{2}.*", "", name)
        return name.strip() or filename

    def _start_youtube(self):
        """Deprecated — pytchat loop lives in community.adapters.youtube."""
        logger.debug("_start_youtube no-op; use Aggregator YouTube adapter")
