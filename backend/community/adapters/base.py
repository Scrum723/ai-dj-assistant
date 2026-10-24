"""ChatAdapter base class for community platform readers."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional

from community.schema import ChatMessage

EmitFn = Callable[[ChatMessage], None]


class BackoffState:
    """Exponential backoff for polling adapters (429 / errors)."""

    def __init__(self, base: float = 1.0, max_sec: float = 60.0):
        self.base = float(base)
        self.max_sec = float(max_sec)
        self.failures = 0
        self.until = 0.0

    def remaining(self) -> float:
        return max(0.0, self.until - time.time())

    def on_success(self) -> None:
        self.failures = 0
        self.until = 0.0

    def on_error(self, *, is_429: bool = False) -> float:
        self.failures += 1
        delay = min(self.max_sec, self.base * (2 ** max(0, self.failures - 1)))
        if is_429:
            delay = max(delay, min(self.max_sec, self.base * 4))
        self.until = time.time() + delay
        return delay


class ChatAdapter(ABC):
    """Read-only by default. Subclasses may set can_send=True and implement send()."""

    name: str = "base"
    can_send: bool = False

    def __init__(self, channel: str = ""):
        self.channel = (channel or "").strip()
        self._emit: Optional[EmitFn] = None
        self._connected = False
        self._error: Optional[str] = None
        self._last_msg_ts: float = 0.0
        self._running = False
        self._backoff = BackoffState()
        self._health: str = "idle"  # idle | ok | degraded | dead

    @abstractmethod
    def start(self, emit: EmitFn) -> None:
        """Begin reading chat; call emit(ChatMessage) for each message."""

    @abstractmethod
    def stop(self) -> None:
        """Stop reading and release resources."""

    def send(self, text: str) -> None:
        raise NotImplementedError(f"{self.name} adapter cannot send")

    def is_ready(self) -> bool:
        """True when adapter has enough config to start (default: non-empty channel)."""
        return bool(self.channel)

    def status(self) -> dict[str, Any]:
        health = self._health
        if self._error and not self._connected and self._running:
            health = "dead"
        elif self._error and self._connected:
            health = "degraded"
        elif self._connected:
            health = "ok"
        elif self._running:
            health = "degraded"
        return {
            "name": self.name,
            "connected": bool(self._connected),
            "error": self._error,
            "last_msg_ts": self._last_msg_ts or None,
            "channel": self.channel,
            "can_send": bool(self.can_send),
            "running": bool(self._running),
            "health": health,
            "backoff_sec": round(self._backoff.remaining(), 2),
        }

    def _note_emit(self, msg: ChatMessage) -> None:
        self._last_msg_ts = msg.ts or time.time()
        if self._emit:
            self._emit(msg)

    def set_channel(self, channel: str) -> None:
        self.channel = (channel or "").strip()
