"""Relay room/Halo lines into platform chats with loop prevention + rate caps.

Off by default. Rules: community.relay.{platform}: {halo, room, max_per_min}.
Room → "[room] Name: text"; Halo → plain text.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Optional

from community.schema import ChatMessage

logger = logging.getLogger(__name__)

RELAY_PLATFORMS = ("twitch", "youtube_official", "kick", "facebook")
SOURCE_PLATFORMS = frozenset({"room", "halo"})
DEFAULT_MAX_PER_MIN = 10
ECHO_MAX = 500
ECHO_TTL_SEC = 120.0


class TokenBucket:
    """Per-platform send budget: capacity = max_per_min, refill 1/min rate."""

    def __init__(self, max_per_min: float = DEFAULT_MAX_PER_MIN, *, clock: Optional[Callable[[], float]] = None):
        self._clock = clock or time.time
        self.max_per_min = max(1.0, float(max_per_min))
        self.tokens = self.max_per_min
        self.updated = self._clock()
        self._lock = threading.Lock()

    def configure(self, max_per_min: float) -> None:
        with self._lock:
            self.max_per_min = max(1.0, float(max_per_min))
            self.tokens = min(self.tokens, self.max_per_min)

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self.updated)
        self.updated = now
        self.tokens = min(self.max_per_min, self.tokens + elapsed * (self.max_per_min / 60.0))

    def try_take(self, n: float = 1.0) -> bool:
        with self._lock:
            self._refill()
            if self.tokens >= n:
                self.tokens -= n
                return True
            return False


class EchoMap:
    """Remember outbound relay texts/ids so Aggregator drops platform echoes."""

    def __init__(self, maxlen: int = ECHO_MAX, ttl: float = ECHO_TTL_SEC, *, clock: Optional[Callable[[], float]] = None):
        self.maxlen = maxlen
        self.ttl = ttl
        self._clock = clock or time.time
        self._by_key: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.Lock()

    def _prune(self) -> None:
        now = self._clock()
        while self._by_key:
            k, ts = next(iter(self._by_key.items()))
            if now - ts <= self.ttl and len(self._by_key) <= self.maxlen:
                break
            if now - ts > self.ttl or len(self._by_key) > self.maxlen:
                self._by_key.popitem(last=False)
            else:
                break
        while len(self._by_key) > self.maxlen:
            self._by_key.popitem(last=False)

    def mark(self, platform: str, *, text: str = "", message_id: str = "") -> None:
        now = self._clock()
        plat = (platform or "").strip().lower()
        with self._lock:
            if message_id:
                self._by_key[f"id:{plat}:{message_id}"] = now
            norm = _norm_text(text)
            if norm:
                self._by_key[f"text:{plat}:{norm}"] = now
            self._prune()

    def is_echo(self, platform: str, *, text: str = "", message_id: str = "") -> bool:
        plat = (platform or "").strip().lower()
        with self._lock:
            self._prune()
            if message_id and f"id:{plat}:{message_id}" in self._by_key:
                return True
            norm = _norm_text(text)
            if norm and f"text:{plat}:{norm}" in self._by_key:
                return True
            return False


def _norm_text(text: str) -> str:
    return " ".join((text or "").strip().split()).lower()


def format_relay_text(msg: ChatMessage) -> str:
    """Room → '[room] Name: text'; Halo → plain text."""
    text = (msg.text or "").strip()
    plat = (msg.platform or "").strip().lower()
    if plat == "halo":
        return text[:500]
    name = ""
    if msg.user:
        name = (msg.user.display_name or msg.user.handle or "").strip()
    name = name or "Fan"
    if plat == "room":
        return f"[room] {name}: {text}"[:500]
    return f"[{plat}] {name}: {text}"[:500]


def default_relay_rules() -> dict[str, dict[str, Any]]:
    return {
        p: {"halo": False, "room": False, "max_per_min": DEFAULT_MAX_PER_MIN}
        for p in RELAY_PLATFORMS
    }


class CommunityRelay:
    """Fan room/Halo messages to adapters that can_send, with caps + echo tags."""

    def __init__(
        self,
        *,
        get_settings: Optional[Callable[[], dict]] = None,
        get_adapters: Optional[Callable[[], dict]] = None,
        clock: Optional[Callable[[], float]] = None,
    ):
        self._get_settings = get_settings or (lambda: {})
        self._get_adapters = get_adapters or (lambda: {})
        self._clock = clock or time.time
        self.echoes = EchoMap(clock=self._clock)
        self._buckets: dict[str, TokenBucket] = {
            p: TokenBucket(DEFAULT_MAX_PER_MIN, clock=self._clock) for p in RELAY_PLATFORMS
        }
        self._lock = threading.Lock()
        self.sent_log: list[dict[str, Any]] = []  # test/observability

    def set_get_adapters(self, fn: Callable[[], dict]) -> None:
        self._get_adapters = fn

    def rules_for(self, platform: str) -> dict[str, Any]:
        plat = (platform or "").strip().lower()
        raw = ((self._get_settings().get("community") or {}).get("relay") or {}).get(plat) or {}
        if not isinstance(raw, dict):
            raw = {}
        return {
            "halo": bool(raw.get("halo")),
            "room": bool(raw.get("room")),
            "max_per_min": max(1, int(raw.get("max_per_min") or DEFAULT_MAX_PER_MIN)),
        }

    def is_echo(self, msg: ChatMessage) -> bool:
        plat = (msg.platform or "").strip().lower()
        if plat not in RELAY_PLATFORMS and plat != "youtube":
            # youtube_official echoes arrive as platform youtube_official (or youtube)
            pass
        return self.echoes.is_echo(plat, text=msg.text or "", message_id=msg.id or "")

    def should_send(self, target: str, source: ChatMessage) -> bool:
        src = (source.platform or "").strip().lower()
        if src not in SOURCE_PLATFORMS:
            return False
        rules = self.rules_for(target)
        if src == "halo" and not rules["halo"]:
            return False
        if src == "room" and not rules["room"]:
            return False
        return True

    def handle(self, msg: ChatMessage) -> list[str]:
        """Relay a successfully ingested room/Halo message. Returns platforms sent."""
        src = (msg.platform or "").strip().lower()
        if src not in SOURCE_PLATFORMS:
            return []
        text = format_relay_text(msg)
        if not text.strip():
            return []
        adapters = self._get_adapters() or {}
        sent: list[str] = []
        for target in RELAY_PLATFORMS:
            if not self.should_send(target, msg):
                continue
            adapter = adapters.get(target)
            if adapter is None or not getattr(adapter, "can_send", False):
                continue
            rules = self.rules_for(target)
            bucket = self._buckets.setdefault(
                target, TokenBucket(rules["max_per_min"], clock=self._clock)
            )
            bucket.configure(rules["max_per_min"])
            if not bucket.try_take(1.0):
                logger.info("relay rate-cap drop platform=%s", target)
                continue
            try:
                result = adapter.send(text)
                out_id = ""
                if isinstance(result, dict):
                    out_id = str(result.get("id") or "")
                elif isinstance(result, str):
                    out_id = result
                self.echoes.mark(target, text=text, message_id=out_id)
                # Also mark under youtube alias if official
                if target == "youtube_official":
                    self.echoes.mark("youtube", text=text, message_id=out_id)
                sent.append(target)
                self.sent_log.append(
                    {"platform": target, "text": text, "id": out_id, "ts": self._clock()}
                )
                if len(self.sent_log) > 200:
                    self.sent_log = self.sent_log[-100:]
            except Exception:
                logger.exception("relay send failed platform=%s", target)
        return sent

    def status(self) -> dict[str, Any]:
        out = {}
        for p in RELAY_PLATFORMS:
            rules = self.rules_for(p)
            out[p] = {
                **rules,
                "enabled": bool(rules["halo"] or rules["room"]),
            }
        return out
