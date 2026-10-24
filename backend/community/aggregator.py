"""Chat aggregator: adapters + room posts → dedupe → moderate → fan-out."""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import OrderedDict, deque
from typing import Any, Callable, Optional

from community.adapters.base import ChatAdapter
from community.schema import SUPPORT_KINDS, ChatMessage, ChatUser
from moderation import check_input

logger = logging.getLogger(__name__)

DEDUPE_MAX = 2000
HISTORY_MAX = 200
LINK_RE = re.compile(r"^!link\s+([A-Za-z0-9]{6})\s*$", re.IGNORECASE)

NoteChatFn = Callable[[dict], None]
CohostIngestFn = Callable[..., None]
BroadcastFn = Callable[[dict], None]


# Forward ref for typing without hard import cycle at module load in tests
StoreLike = Any
IdentityLike = Any
ModLike = Any


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
        identity: Optional[IdentityLike] = None,
        mod: Optional[ModLike] = None,
        on_linked: Optional[Callable[[dict], None]] = None,
        relay: Optional[Any] = None,
    ):
        self._adapters: dict[str, ChatAdapter] = {}
        self._cohost_ingest = cohost_ingest
        self._note_chat = note_chat
        self._room_broadcast = room_broadcast
        self._get_settings = get_settings or (lambda: {})
        self._store = store
        self._identity = identity
        self._mod = mod
        self._on_linked = on_linked
        self._relay = relay
        self._dedupe = _LRUSet()
        self._history: deque[ChatMessage] = deque(maxlen=HISTORY_MAX)
        self._lock = threading.Lock()
        self._started = False

    @property
    def store(self) -> Optional[StoreLike]:
        return self._store

    def set_store(self, store: Optional[StoreLike]) -> None:
        self._store = store

    def set_identity(self, identity: Optional[IdentityLike]) -> None:
        self._identity = identity

    def set_mod(self, mod: Optional[ModLike]) -> None:
        self._mod = mod

    def set_relay(self, relay: Optional[Any]) -> None:
        self._relay = relay
        if relay is not None and hasattr(relay, "set_get_adapters"):
            try:
                relay.set_get_adapters(self.adapters_map)
            except Exception:
                logger.exception("relay set_get_adapters failed")

    def register(self, adapter: ChatAdapter) -> None:
        self._adapters[adapter.name] = adapter

    def get_adapter(self, name: str) -> Optional[ChatAdapter]:
        return self._adapters.get(name)

    def adapters_map(self) -> dict[str, ChatAdapter]:
        return dict(self._adapters)

    def drop_from_history(self, message_id: str) -> bool:
        """Remove a message from in-memory history (delete-message)."""
        mid = (message_id or "").strip()
        if not mid:
            return False
        with self._lock:
            before = len(self._history)
            self._history = deque(
                (m for m in self._history if m.id != mid),
                maxlen=HISTORY_MAX,
            )
            return len(self._history) < before

    def _apply_adapter_config(self, name: str, adapter: ChatAdapter, acfg: dict) -> None:
        """Push settings fields into known adapters without failing unknown ones."""
        channel = str(
            acfg.get("channel")
            or acfg.get("username")
            or acfg.get("post_id")
            or acfg.get("live_video_id")
            or acfg.get("api_url")
            or ""
        ).strip()
        if channel:
            adapter.set_channel(channel)
        configure = getattr(adapter, "configure", None)
        if not callable(configure):
            return
        try:
            if name == "kick":
                configure(
                    channel=channel or None,
                    access_token=str(acfg.get("access_token") or "") or None,
                    broadcaster_user_id=acfg.get("broadcaster_user_id"),
                )
            elif name == "facebook":
                configure(
                    live_video_id=str(acfg.get("live_video_id") or acfg.get("channel") or "") or None,
                    page_access_token=str(acfg.get("page_access_token") or "") or None,
                    poll_interval_sec=acfg.get("poll_interval_sec"),
                )
            elif name == "rumble":
                configure(
                    api_url=str(acfg.get("api_url") or acfg.get("channel") or "") or None,
                    poll_interval_sec=acfg.get("poll_interval_sec"),
                )
            elif name == "youtube_official":
                configure(
                    channel=channel or None,
                    api_key=str(acfg.get("api_key") or "") or None,
                    live_chat_id=str(acfg.get("live_chat_id") or "") or None,
                    daily_quota=acfg.get("daily_quota"),
                    oauth_token=str(acfg.get("oauth_token") or "") or None,
                )
            elif name == "twitch":
                configure(
                    channel=channel or None,
                    bot_token=str(acfg.get("bot_token") or "") or None,
                    bot_nick=str(acfg.get("bot_nick") or "") or None,
                )
            elif name == "tiktok":
                configure(
                    username=str(acfg.get("username") or acfg.get("channel") or "") or None,
                )
            elif name == "x":
                configure(
                    post_id=str(acfg.get("post_id") or acfg.get("channel") or "") or None,
                    bearer_token=str(acfg.get("bearer_token") or "") or None,
                    can_post=acfg.get("can_post"),
                    poll_interval_sec=acfg.get("poll_interval_sec"),
                )
        except Exception:
            logger.exception("configure adapter %s failed", name)

    def start(self) -> None:
        """Start adapters that are enabled in settings. Dead adapters never block others."""
        cfg = (self._get_settings().get("community") or {}).get("adapters") or {}
        for name, adapter in list(self._adapters.items()):
            acfg = cfg.get(name) or {}
            enabled = bool(acfg.get("enabled"))
            try:
                self._apply_adapter_config(name, adapter, acfg)
            except Exception:
                logger.exception("adapter config %s failed", name)
            ready = False
            try:
                ready = bool(adapter.is_ready()) if hasattr(adapter, "is_ready") else bool(adapter.channel)
            except Exception:
                logger.exception("adapter is_ready %s failed", name)
            if enabled and ready:
                try:
                    adapter.start(self.ingest)
                    logger.info("community adapter %s started channel=%s", name, adapter.channel)
                except Exception:
                    logger.exception("failed to start adapter %s", name)
                    try:
                        adapter.stop()
                    except Exception:
                        pass
            else:
                try:
                    adapter.stop()
                except Exception:
                    logger.exception("adapter stop failed: %s", name)
                logger.info(
                    "community adapter %s disabled (enabled=%s ready=%s channel=%r)",
                    name,
                    enabled,
                    ready,
                    adapter.channel,
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
        cfg = (self._get_settings().get("community") or {}).get("adapters") or {}
        acfg = dict(cfg.get(name) or {})
        if channel is not None:
            adapter.set_channel(channel)
            acfg["channel"] = channel
        self._apply_adapter_config(name, adapter, acfg)
        if enabled is None:
            enabled = bool(acfg.get("enabled"))
        ready = bool(adapter.is_ready()) if hasattr(adapter, "is_ready") else bool(adapter.channel)
        if enabled and ready:
            try:
                adapter.start(self.ingest)
            except Exception:
                logger.exception("failed to start adapter %s", name)
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

    def _resolve_profile(self, msg: ChatMessage) -> None:
        if msg.profile_id is not None or self._identity is None:
            return
        try:
            user = msg.user if isinstance(msg.user, ChatUser) else ChatUser()
            pid = self._identity.resolve(
                msg.platform,
                handle=user.handle or user.display_name or "",
                platform_user_id=user.platform_user_id or "",
            )
            if pid is not None:
                msg.profile_id = int(pid)
                name = None
                if hasattr(self._identity, "display_name_for"):
                    name = self._identity.display_name_for(int(pid))
                if name:
                    msg.user.display_name = name
        except Exception:
            logger.exception("identity resolve failed")

    def _handle_link_command(self, msg: ChatMessage) -> bool:
        """If text is !link CODE, consume it. Returns True when handled (skip normal fan-out to Halo as chat)."""
        text = (msg.text or "").strip()
        m = LINK_RE.match(text)
        if not m or self._identity is None:
            return False
        code = m.group(1).upper()
        user = msg.user if isinstance(msg.user, ChatUser) else ChatUser()
        handle = user.handle or user.display_name or ""
        try:
            linked = self._identity.consume_link_code(
                code,
                platform=msg.platform,
                handle=handle,
                platform_user_id=user.platform_user_id or handle,
            )
        except Exception:
            logger.exception("link code consume failed")
            linked = None
        if linked:
            msg.profile_id = int(linked["user_id"])
            name = None
            if hasattr(self._identity, "display_name_for"):
                name = self._identity.display_name_for(int(linked["user_id"]))
            profile_name = name or f"pass #{linked['user_id']}"
            if name:
                msg.user.display_name = name
            if self._on_linked:
                try:
                    self._on_linked(linked)
                except Exception:
                    logger.exception("on_linked hook failed")
            confirm = f"Booth pass linked — hey {profile_name}, you're one person across platforms now."
            try:
                self.publish_halo(confirm)
            except Exception:
                logger.exception("halo link confirm failed")
        else:
            try:
                self.publish_halo("That link code expired or is invalid. Grab a fresh one on your Profile page.")
            except Exception:
                logger.exception("halo link reject failed")
        return True

    def ingest(self, msg: ChatMessage | dict) -> bool:
        """Accept a ChatMessage (or dict). Return True if accepted and fanned out."""
        if isinstance(msg, dict):
            msg = ChatMessage.from_dict(msg)
        if not msg.id:
            msg.id = f"{msg.platform}:{msg.user.handle}:{msg.ts}"
        if self._dedupe.seen(msg.id):
            logger.debug("dedupe drop %s", msg.id)
            return False

        if self._relay is not None and msg.kind != "system":
            try:
                if self._relay.is_echo(msg):
                    logger.info("aggregator relay-echo drop id=%s platform=%s", msg.id, msg.platform)
                    return False
            except Exception:
                logger.exception("relay echo check failed")

        self._resolve_profile(msg)
        link_handled = False
        if msg.kind != "system":
            link_handled = self._handle_link_command(msg)

        if self._mod is not None and msg.kind != "system":
            try:
                user = msg.user if isinstance(msg.user, ChatUser) else ChatUser()
                if self._mod.is_banned(
                    profile_id=msg.profile_id,
                    platform=msg.platform,
                    handle=user.handle or user.display_name or "",
                ):
                    logger.info("aggregator ban drop id=%s profile=%s", msg.id, msg.profile_id)
                    return False
            except Exception:
                logger.exception("ban check failed")

        settings = self._get_settings()
        author = (msg.user.display_name or msg.user.handle or "").strip()
        skip_rate = self._is_operator_author(author, msg.platform, settings)
        # A6 park: blank Super Chat / gift / member / follow still thank
        is_support = (msg.kind or "") in SUPPORT_KINDS
        text_blank = not (msg.text or "").strip()
        if is_support and text_blank:
            ok, reason = True, ""
        else:
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

        if self._cohost_ingest and msg.kind != "system" and not link_handled:
            try:
                author = msg.user.display_name or msg.user.handle or "Fan"
                support = msg.support.to_dict() if msg.support else None
                # A5 carry-over: pass kind so Super Chats/subs/gifts hit supporter priority
                kwargs = {
                    "platform": msg.platform,
                    "from_aggregator": True,
                    "event_kind": msg.kind,
                    "support": support,
                    "profile_id": msg.profile_id,
                }
                try:
                    self._cohost_ingest(author, msg.text or "", **kwargs)
                except TypeError:
                    # Older ingest without support / event_kind
                    try:
                        self._cohost_ingest(
                            author,
                            msg.text or "",
                            platform=msg.platform,
                            from_aggregator=True,
                            event_kind=msg.kind,
                        )
                    except TypeError:
                        self._cohost_ingest(
                            author, msg.text or "", platform=msg.platform, from_aggregator=True
                        )
            except TypeError:
                try:
                    self._cohost_ingest(author, msg.text or "", platform=msg.platform)
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

        if self._relay is not None and (msg.platform or "").strip().lower() in ("room", "halo"):
            try:
                self._relay.handle(msg)
            except Exception:
                logger.exception("relay handle failed")

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
            st["configured_channel"] = str(
                acfg.get("channel")
                or acfg.get("username")
                or acfg.get("post_id")
                or acfg.get("live_video_id")
                or acfg.get("api_url")
                or ""
            )
            adapters[name] = st
        alerts = list(getattr(self, "_operator_alerts", []) or [])[-20:]
        relay_status = None
        if self._relay is not None and hasattr(self._relay, "status"):
            try:
                relay_status = self._relay.status()
            except Exception:
                relay_status = None
        return {
            "started": self._started,
            "history_len": len(self._history),
            "adapters": adapters,
            "operator_alerts": alerts,
            "relay": relay_status,
        }
