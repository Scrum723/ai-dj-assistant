"""TikTok LIVE chat adapter (experimental).

TikTok has no official LIVE chat API for general developers. This adapter
optionally uses the community TikTokLive webcast client (unofficial; may
break or violate TikTok terms). Disabled by default.

Docs / library (skimmed 2026-10-24):
- https://github.com/isaackogan/TikTokLive — unofficial webcast reader
- No official TikTok LIVE chat read API for third-party booths

Settings: community.adapters.tiktok.{enabled:false, username}
can_send=False always.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional

from community.adapters.base import ChatAdapter, EmitFn
from community.adapters.experimental import AlertFn, DisableFn, ExperimentalMixin
from community.schema import ChatMessage, ChatUser, SupportInfo

logger = logging.getLogger(__name__)

try:
    from TikTokLive import TikTokLiveClient  # type: ignore

    TIKTOK_LIVE_AVAILABLE = True
except Exception:  # ImportError and broken optional deps
    TikTokLiveClient = None  # type: ignore
    TIKTOK_LIVE_AVAILABLE = False


def map_tiktok_event(event: dict[str, Any], channel: str = "") -> Optional[ChatMessage]:
    """Map TikTok gift / follow / join / chat fixtures to ChatMessage."""
    if not isinstance(event, dict):
        return None
    etype = str(event.get("type") or event.get("event") or "chat").strip().lower()
    user_raw = event.get("user") or event.get("sender") or {}
    if not isinstance(user_raw, dict):
        user_raw = {"nickname": str(user_raw)}
    handle = str(
        user_raw.get("uniqueId")
        or user_raw.get("unique_id")
        or user_raw.get("username")
        or user_raw.get("nickname")
        or "Fan"
    ).strip()[:40] or "Fan"
    display = str(user_raw.get("nickname") or user_raw.get("display_name") or handle)[:40]
    uid = str(user_raw.get("userId") or user_raw.get("user_id") or user_raw.get("id") or handle)
    msg_id = str(event.get("id") or event.get("msgId") or f"{uid}:{time.time()}")
    text = str(event.get("comment") or event.get("text") or event.get("message") or "")

    kind = "chat"
    support: Optional[SupportInfo] = None
    if etype in {"gift", "webcastgiftmessage", "gift_message"}:
        kind = "gift"
        repeat = event.get("repeat_count") or event.get("repeatCount") or event.get("count") or 1
        try:
            count_i = max(1, int(repeat))
        except (TypeError, ValueError):
            count_i = 1
        diamonds = event.get("diamond_count") or event.get("diamondCount") or event.get("diamond_value")
        try:
            amount = float(diamonds) if diamonds is not None else None
        except (TypeError, ValueError):
            amount = None
        gift_name = event.get("gift_name") or event.get("giftName") or event.get("gift")
        tier = str(gift_name) if gift_name else None
        support = SupportInfo(
            amount=amount,
            currency="diamonds" if amount is not None else None,
            tier=tier,
            count=count_i,
        )
        if not text:
            label = tier or "gift"
            text = f"sent {label}" + (f" x{count_i}" if count_i > 1 else "")
    elif etype in {"join", "webcastmembermessage", "member_message"}:
        kind = "member"
        support = SupportInfo(count=1)
        if not text:
            text = "joined"
    elif etype in {"follow", "member", "social"}:
        action = str(event.get("action") or etype).lower()
        if "follow" in action or etype == "follow":
            kind = "follow"
            support = SupportInfo(count=1)
            if not text:
                text = "followed"
        elif etype == "member" or "join" in action:
            kind = "member"
            support = SupportInfo(count=1)
            if not text:
                text = "joined"
        else:
            kind = "chat"
    elif etype in {"comment", "chat", "webcastchatmessage"}:
        kind = "chat"

    return ChatMessage(
        id=f"tiktok:{msg_id}",
        platform="tiktok",
        channel=channel or str(event.get("channel") or ""),
        user=ChatUser(
            platform_user_id=uid,
            handle=handle,
            display_name=display,
        ),
        text=text,
        kind=kind,
        ts=float(event.get("ts") or time.time()),
        support=support,
        raw={"type": etype, "id": msg_id},
    )


def _user_dict_from_obj(user: Any) -> dict[str, Any]:
    if user is None:
        return {}
    if isinstance(user, dict):
        return user
    return {
        "uniqueId": getattr(user, "unique_id", None) or getattr(user, "uniqueId", None),
        "nickname": getattr(user, "nickname", None),
        "userId": getattr(user, "id", None) or getattr(user, "user_id", None),
    }


def event_obj_to_dict(event: Any, etype: str) -> dict[str, Any]:
    """Normalize a TikTokLive event object (or dict) into map_tiktok_event shape."""
    if isinstance(event, dict):
        out = dict(event)
        out.setdefault("type", etype)
        return out
    user = getattr(event, "user", None) or getattr(event, "from_user", None)
    data: dict[str, Any] = {
        "type": etype,
        "user": _user_dict_from_obj(user),
        "id": str(getattr(event, "msg_id", None) or getattr(event, "id", None) or time.time()),
    }
    if etype in {"comment", "chat"}:
        data["comment"] = str(getattr(event, "comment", None) or getattr(event, "text", "") or "")
    elif etype == "gift":
        gift = getattr(event, "gift", None)
        gift_name = None
        if gift is not None:
            gift_name = getattr(gift, "name", None) or getattr(gift, "extended_gift", None)
            if hasattr(gift_name, "name"):
                gift_name = gift_name.name
        data["gift_name"] = gift_name or getattr(event, "gift_name", None)
        data["repeat_count"] = getattr(event, "repeat_count", None) or 1
        diamonds = getattr(event, "value", None)
        if diamonds is None and gift is not None:
            diamonds = getattr(gift, "diamond_count", None)
        data["diamond_count"] = diamonds
    return data


ClientFactory = Callable[[str], Any]


class TikTokAdapter(ChatAdapter, ExperimentalMixin):
    name = "tiktok"
    can_send = False
    experimental = True

    def __init__(
        self,
        channel: str = "",
        *,
        username: str = "",
        on_auto_disable: Optional[DisableFn] = None,
        on_alert: Optional[AlertFn] = None,
        client_factory: Optional[ClientFactory] = None,
    ):
        super().__init__(channel=channel or username)
        self._init_experimental(on_auto_disable=on_auto_disable, on_alert=on_alert)
        self.username = (username or channel or "").strip().lstrip("@")
        if self.username:
            self.channel = self.username
        self._client_factory = client_factory
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._client: Any = None

    def configure(self, *, username: Optional[str] = None, channel: Optional[str] = None) -> None:
        name = username if username is not None else channel
        if name is not None:
            self.username = str(name).strip().lstrip("@")
            self.channel = self.username

    def is_ready(self) -> bool:
        if self._auto_disabled:
            return False
        if not self.username:
            return False
        if self._client_factory is not None:
            return True
        return bool(TIKTOK_LIVE_AVAILABLE)

    def start(self, emit: EmitFn) -> None:
        self.stop()
        self._emit = emit
        self._stop = threading.Event()
        self._running = True
        self._error = None
        if self._auto_disabled:
            self._connected = False
            self._error = "auto-disabled after repeated failures"
            self._running = False
            self._health = "dead"
            return
        if not self.username:
            self._connected = False
            self._error = "missing tiktok username"
            self._running = False
            self._health = "dead"
            return
        if self._client_factory is None and not TIKTOK_LIVE_AVAILABLE:
            self._connected = False
            self._error = "optional TikTokLive package not installed"
            self._running = False
            self._health = "dead"
            self._note_failure(self._error)
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="tiktok-live")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._running = False
        self._connected = False
        client = self._client
        self._client = None
        if client is not None:
            for meth in ("stop", "disconnect", "close"):
                fn = getattr(client, meth, None)
                if callable(fn):
                    try:
                        fn()
                    except Exception:
                        pass
                    break
        self._thread = None
        self._health = "idle"

    def status(self) -> dict[str, Any]:
        st = super().status()
        st.update(self.experimental_status())
        st["username"] = self.username
        st["tiktok_live_available"] = bool(TIKTOK_LIVE_AVAILABLE or self._client_factory)
        return st

    def _emit_mapped(self, payload: dict[str, Any]) -> None:
        msg = map_tiktok_event(payload, channel=self.username)
        if msg:
            self._note_emit(msg)

    def _bind_client(self, client: Any) -> None:
        """Attach handlers for comment/gift/join/follow. Supports dict inject + TikTokLive."""

        def handle(etype: str):
            def _inner(event: Any) -> None:
                try:
                    self._emit_mapped(event_obj_to_dict(event, etype))
                except Exception:
                    logger.exception("tiktok map failed for %s", etype)

            return _inner

        # Injectable fake: .on(name, cb) or push via .handlers
        if hasattr(client, "on") and callable(client.on):
            for etype in ("comment", "gift", "join", "follow"):
                try:
                    client.on(etype, handle(etype))
                except TypeError:
                    # Newer TikTokLive uses event classes; ignore and use add_listener below
                    pass
        add = getattr(client, "add_listener", None)
        if callable(add):
            try:
                from TikTokLive.events import (  # type: ignore
                    CommentEvent,
                    FollowEvent,
                    GiftEvent,
                    JoinEvent,
                )

                add(CommentEvent, handle("comment"))
                add(GiftEvent, handle("gift"))
                add(JoinEvent, handle("join"))
                add(FollowEvent, handle("follow"))
            except Exception:
                logger.debug("TikTokLive event class bind skipped", exc_info=True)

    def _loop(self) -> None:
        logger.info("TikTok experimental adapter starting for @%s", self.username)
        try:
            if self._client_factory:
                client = self._client_factory(self.username)
            else:
                assert TikTokLiveClient is not None
                unique = self.username if self.username.startswith("@") else f"@{self.username}"
                client = TikTokLiveClient(unique_id=unique)
            self._client = client
            self._bind_client(client)
            self._connected = True
            self._error = None
            self._health = "ok"
            self._note_success()

            # Prefer non-blocking start + wait on stop; fall back to run()
            started = False
            start_fn = getattr(client, "start", None)
            if callable(start_fn):
                try:
                    import asyncio

                    loop = asyncio.new_event_loop()
                    try:
                        res = start_fn()
                        if asyncio.iscoroutine(res):
                            loop.run_until_complete(res)
                        started = True
                        while self._running and not self._stop.is_set():
                            self._stop.wait(0.5)
                    finally:
                        loop.close()
                except Exception as e:
                    logger.warning("tiktok client.start failed: %s", e)

            if not started and not self._stop.is_set():
                run_fn = getattr(client, "run", None)
                if callable(run_fn):
                    # run() blocks; stop() should unblock via client.stop
                    t = threading.Thread(target=run_fn, daemon=True, name="tiktok-run")
                    t.start()
                    while self._running and not self._stop.is_set() and t.is_alive():
                        self._stop.wait(0.5)
                else:
                    # Pure inject fake that pushes via simulate
                    while self._running and not self._stop.is_set():
                        pump = getattr(client, "pump", None)
                        if callable(pump):
                            try:
                                pump()
                                self._note_success()
                            except Exception as e:
                                self._error = str(e)
                                self._connected = False
                                self._health = "dead"
                                if self._note_failure(str(e)):
                                    return
                        self._stop.wait(0.5)
        except Exception as e:
            self._error = str(e)
            self._connected = False
            self._health = "dead"
            logger.exception("TikTok adapter failed")
            self._note_failure(str(e))
        finally:
            self._connected = False
            self._running = False
