"""Rumble Live Stream API chat poller (read-only).

Docs (skimmed 2026-10-24):
- https://rumble.support/help/how-to-use-rumble-s-live-stream-api (updated 2025-11-20)
  Creator URL from https://rumble.com/account/livestream-api embeds user id + key;
  no separate auth. Chat under livestreams[].chat.recent_messages / recent_rants
  (max 50). Values only populated while live. Read-only — no send API.

Settings: community.adapters.rumble.{enabled, api_url, poll_interval_sec}
Env: RUMBLE_LIVESTREAM_API_URL
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from typing import Any, Optional

from community.adapters.base import ChatAdapter, EmitFn
from community.schema import ChatMessage, ChatUser, SupportInfo

logger = logging.getLogger(__name__)


def _parse_ts(value: Any) -> float:
    if value is None:
        return time.time()
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    try:
        from datetime import datetime

        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return time.time()


def _msg_key(username: str, text: str, created_on: str, kind: str) -> str:
    raw = f"{kind}|{username}|{created_on}|{text}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def map_rumble_chat_message(
    item: dict[str, Any],
    *,
    stream_id: str = "",
    kind: str = "chat",
) -> Optional[ChatMessage]:
    """Map Rumble recent_messages / recent_rants item to ChatMessage."""
    if not isinstance(item, dict):
        return None
    username = str(item.get("username") or item.get("user") or "Fan").strip()[:40] or "Fan"
    text = str(item.get("text") or item.get("message") or "")
    created = str(item.get("created_on") or "")
    badges = item.get("badges") if isinstance(item.get("badges"), list) else []
    badge_names = [str(b) for b in badges if b]
    mid = _msg_key(username, text, created, kind)
    support: Optional[SupportInfo] = None
    msg_kind = "chat"
    if kind == "rant":
        msg_kind = "super_chat"
        dollars = item.get("amount_dollars")
        cents = item.get("amount_cents")
        amount = None
        try:
            if dollars is not None:
                amount = float(dollars)
            elif cents is not None:
                amount = float(cents) / 100.0
        except (TypeError, ValueError):
            amount = None
        support = SupportInfo(amount=amount, currency="USD", count=1)
    return ChatMessage(
        id=f"rumble:{mid}",
        platform="rumble",
        channel=stream_id,
        user=ChatUser(
            platform_user_id=username,
            handle=username,
            display_name=username,
            badges=badge_names,
            is_mod="admin" in badge_names or "moderator" in badge_names,
        ),
        text=text,
        kind=msg_kind,
        ts=_parse_ts(created),
        support=support,
        raw={"kind": kind, "created_on": created},
    )


def extract_rumble_messages(payload: dict[str, Any]) -> list[ChatMessage]:
    """Pull chat + rants from a Rumble livestream API JSON payload."""
    out: list[ChatMessage] = []
    if not isinstance(payload, dict):
        return out
    streams = payload.get("livestreams") or []
    if not isinstance(streams, list):
        return out
    for stream in streams:
        if not isinstance(stream, dict):
            continue
        sid = str(stream.get("id") or "")
        chat = stream.get("chat") if isinstance(stream.get("chat"), dict) else {}
        for item in chat.get("recent_messages") or []:
            msg = map_rumble_chat_message(item, stream_id=sid, kind="chat")
            if msg:
                out.append(msg)
        for item in chat.get("recent_rants") or []:
            msg = map_rumble_chat_message(item, stream_id=sid, kind="rant")
            if msg:
                out.append(msg)
    return out


class RumbleAdapter(ChatAdapter):
    name = "rumble"
    can_send = False

    def __init__(
        self,
        channel: str = "",
        *,
        api_url: str = "",
        poll_interval_sec: float = 3.0,
        http_get=None,
    ):
        super().__init__(channel=channel)
        self.api_url = (api_url or os.getenv("RUMBLE_LIVESTREAM_API_URL") or channel or "").strip()
        self.poll_interval_sec = max(1.0, float(poll_interval_sec))
        self._http_get = http_get
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._seen: set[str] = set()

    def configure(
        self,
        *,
        api_url: Optional[str] = None,
        poll_interval_sec: Optional[float] = None,
    ) -> None:
        if api_url is not None:
            self.api_url = api_url.strip()
            if self.api_url:
                self.channel = self.api_url
        if poll_interval_sec is not None:
            self.poll_interval_sec = max(1.0, float(poll_interval_sec))

    def is_ready(self) -> bool:
        return bool(self.api_url)

    def start(self, emit: EmitFn) -> None:
        self.stop()
        self._emit = emit
        self._stop = threading.Event()
        self._running = True
        self._error = None
        if not self.is_ready():
            self._connected = False
            self._error = "missing RUMBLE_LIVESTREAM_API_URL / api_url"
            self._running = False
            self._health = "dead"
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="rumble-api")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._running = False
        self._connected = False
        self._thread = None
        self._health = "idle"

    def _get(self, url: str) -> Any:
        if self._http_get:
            return self._http_get(url)
        import requests

        return requests.get(url, timeout=15)

    def _loop(self) -> None:
        logger.info("Rumble adapter polling livestream API")
        while self._running and not self._stop.is_set():
            wait = self._backoff.remaining()
            if wait > 0:
                self._stop.wait(min(wait, 5.0))
                continue
            try:
                res = self._get(self.api_url)
                status = getattr(res, "status_code", 200)
                if status == 429:
                    delay = self._backoff.on_error(is_429=True)
                    self._error = f"429 backoff {delay:.1f}s"
                    self._health = "degraded"
                    self._stop.wait(min(delay, 5.0))
                    continue
                if status >= 400:
                    self._backoff.on_error()
                    self._error = f"HTTP {status}"
                    self._connected = False
                    self._health = "dead"
                    self._stop.wait(self.poll_interval_sec)
                    continue
                data = res.json() if hasattr(res, "json") else res
                self._backoff.on_success()
                self._connected = True
                self._error = None
                self._health = "ok"
                for msg in extract_rumble_messages(data if isinstance(data, dict) else {}):
                    if msg.id in self._seen:
                        continue
                    self._seen.add(msg.id)
                    self._note_emit(msg)
                if len(self._seen) > 5000:
                    self._seen = set(list(self._seen)[-2500:])
            except Exception as e:
                self._backoff.on_error()
                self._error = str(e)
                self._connected = False
                self._health = "dead"
                logger.exception("Rumble adapter poll failed")
            self._stop.wait(self.poll_interval_sec)
        self._connected = False
        self._running = False
