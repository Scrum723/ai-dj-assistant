"""ChatAdapter base class for community platform readers."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional

from community.schema import ChatMessage

EmitFn = Callable[[ChatMessage], None]


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

    @abstractmethod
    def start(self, emit: EmitFn) -> None:
        """Begin reading chat; call emit(ChatMessage) for each message."""

    @abstractmethod
    def stop(self) -> None:
        """Stop reading and release resources."""

    def send(self, text: str) -> None:
        raise NotImplementedError(f"{self.name} adapter cannot send")

    def status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "connected": bool(self._connected),
            "error": self._error,
            "last_msg_ts": self._last_msg_ts or None,
            "channel": self.channel,
            "can_send": bool(self.can_send),
            "running": bool(self._running),
        }

    def _note_emit(self, msg: ChatMessage) -> None:
        self._last_msg_ts = msg.ts or time.time()
        if self._emit:
            self._emit(msg)

    def set_channel(self, channel: str) -> None:
        self.channel = (channel or "").strip()
