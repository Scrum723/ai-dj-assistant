"""Chat aggregator: adapters + room posts → dedupe → moderate → fan-out."""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict, deque
from typing import Any, Callable, Optional

from community.adapters.base import ChatAdapter
from community.schema import ChatMessage, ChatUser
from moderation import check_input

logger = logging.getLogger(__name__)

DEDUPE_MAX = 2000
HISTORY_MAX = 200

NoteChatFn = Callable[[dict], None]
CohostIngestFn = Callable[..., None]
BroadcastFn = Callable[[dict], None]


# Forward ref for typing without hard import cycle at module load in tests
StoreLike = Any


class _LRUSet:
    def __init__(self, maxlen: int = DEDUPE_MAX):
        self.maxlen = maxlen
        self._data: OrderedDict[str, None] = OrderedDict()
        self._lock = threading.Lock()

    def seen(self, key: str) -> bool:
        """Return True if already present; else insert and return False."""
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return True
            self._data[key] = None
            while len(self._data) > self.maxlen:
                self._data.popitem(last=False)
            return False


class Aggregator:
    def __init__(
        self,
        *,
        cohost_ingest: Optional[CohostIngestFn] = None,
        note_chat: Optional[NoteChatFn] = None,
        room_broadcast: Optional[BroadcastFn] = None,
        get_settings: Optional[Callable[[], dict]] = None,
        store: Optional[StoreLike] = None,
    ):
        self._adapters: dict[str, ChatAdapter] = {}
        self._cohost_ingest = cohost_ingest
        self._note_chat = note_chat
        self._room_broadcast = room_broadcast
        self._get_settings = get_settings or (lambda: {})
        self._store = store
        self._dedupe = _LRUSet()
        self._history: deque[ChatMessage] = deque(maxlen=HISTORY_MAX)
        self._lock = threading.Lock()
        self._started = False

    @property
    def store(self) -> Optional[StoreLike]:
        return self._store

    def set_store(self, store: Optional[StoreLike]) -> None:
        self._store = store

    def register(self, adapter: ChatAdapter) -> None:
        self._adapters[adapter.name] = adapter

    def get_adapter(self, name: str) -> Optional[ChatAdapter]:
        return self._adapters.get(name)

    def start(self) -> None:
        """Start adapters that are enabled in settings."""
        cfg = (self._get_settings().get("community") or {}).get("adapters") or {}
        for name, adapter in self._adapters.items():
            acfg = cfg.get(name) or {}
            enabled = bool(acfg.get("enabled"))
            channel = str(acfg.get("channel") or "").strip()
            if channel:
                adapter.set_channel(channel)
            if enabled and channel:
                try:
                    adapter.start(self.ingest)
                    logger.info("community adapter %s started channel=%s", name, channel)
                except Exception:
                    logger.exception("failed to start adapter %s", name)
            else:
                adapter.stop()
                logger.info(
                    "community adapter %s disabled (enabled=%s channel=%r)",
                    name,
                    enabled,
                    channel,
                )
        self._started = True

    def stop(self) -> None:
        for adapter in self._adapters.values():
            try:
                adapter.stop()
            except Exception:
                logger.exception("adapter stop failed: %s", adapter.name)
        self._started = False

    def configure_adapter(self, name: str, *, enabled: Optional[bool] = None, channel: Optional[str] = None) -> None:
        adapter = self._adapters.get(name)
        if not adapter:
            return
        if channel is not None:
            adapter.set_channel(channel)
        cfg = (self._get_settings().get("community") or {}).get("adapters") or {}
        acfg = dict(cfg.get(name) or {})
        if enabled is None:
            enabled = bool(acfg.get("enabled"))
        ch = adapter.channel or str(acfg.get("channel") or "")
        if enabled and ch:
            adapter.start(self.ingest)
        else:
            adapter.stop()

    @staticmethod
    def _is_operator_author(author: str, platform: str, settings: dict) -> bool:
        """Match halo.operator_handles (list or per-platform dict). Rate skip only."""
        key = (author or "").strip().lower()
        if not key:
            return False
        raw = (settings.get("halo") or {}).get("operator_handles")
        plat = (platform or "").strip().lower()
        handles: set[str] = set()
        if isinstance(raw, dict) and raw:
            val = raw.get(plat) or raw.get(platform or "")
            if isinstance(val, (list, tuple)):
                handles = {str(h).strip().lower() for h in val if str(h).strip()}
            elif val is not None and str(val).strip():
                handles = {str(val).strip().lower()}
        elif isinstance(raw, list) and raw:
            handles = {str(h).strip().lower() for h in raw if str(h).strip()}
        else:
            handles = {"charles", "charlesclottin"}
        return key in handles

    def ingest(self, msg: ChatMessage | dict) -> bool:
        """Accept a ChatMessage (or dict). Return True if accepted and fanned out."""
        if isinstance(msg, dict):
            msg = ChatMessage.from_dict(msg)
        if not msg.id:
            msg.id = f"{msg.platform}:{msg.user.handle}:{msg.ts}"
        if self._dedupe.seen(msg.id):
            logger.debug("dedupe drop %s", msg.id)
            return False

        settings = self._get_settings()
        author = (msg.user.display_name or msg.user.handle or "").strip()
        skip_rate = self._is_operator_author(author, msg.platform, settings)
        ok, reason = check_input(msg.to_client(), settings=settings, skip_rate=skip_rate)
        if not ok:
            logger.info("aggregator moderation drop id=%s reason=%s", msg.id, reason)
            return False

        with self._lock:
            self._history.append(msg)

        if self._store is not None:
            try:
                self._store.insert_message(msg)
            except Exception:
                logger.exception("community store persist failed id=%s", msg.id)

        client = msg.to_client()
        legacy = msg.to_legacy()

        if self._cohost_ingest:
            try:
                author = msg.user.display_name or msg.user.handle or "Fan"
                self._cohost_ingest(
                    author,
                    msg.text,
                    platform=msg.platform,
                    from_aggregator=True,
                )
            except TypeError:
                # Older ingest without from_aggregator
                try:
                    self._cohost_ingest(author, msg.text, platform=msg.platform)
                except Exception:
                    logger.exception("cohost ingest failed")
            except Exception:
                logger.exception("cohost ingest failed")

        if self._note_chat:
            try:
                self._note_chat(legacy)
            except Exception:
                logger.exception("note_chat failed")

        if self._room_broadcast:
            try:
                self._room_broadcast({"type": "chat", "payload": client})
            except Exception:
                logger.exception("room broadcast failed")

        return True

    def publish_halo(self, text: str, *, name: str = "Halo") -> bool:
        """Publish a Halo line into the merged room (platform=halo)."""
        ts = time.time()
        handle = (name or "Halo").strip()[:40] or "Halo"
        msg = ChatMessage(
            id=f"halo:{handle}:{ts}",
            platform="halo",
            channel="booth",
            user=ChatUser(
                platform_user_id="halo",
                handle=handle,
                display_name=handle,
                is_owner=True,
            ),
            text=(text or "").strip()[:280],
            kind="chat",
            ts=ts,
        )
        return self.ingest(msg)

    def recent(self, limit: int = 50) -> list[dict]:
        with self._lock:
            items = list(self._history)[-limit:]
        return [m.to_client() for m in items]

    def status(self) -> dict[str, Any]:
        cfg = (self._get_settings().get("community") or {}).get("adapters") or {}
        adapters = {}
        for name, adapter in self._adapters.items():
            st = adapter.status()
            acfg = cfg.get(name) or {}
            st["enabled"] = bool(acfg.get("enabled"))
            st["configured_channel"] = str(acfg.get("channel") or "")
            adapters[name] = st
        return {
            "started": self._started,
            "history_len": len(self._history),
            "adapters": adapters,
        }
