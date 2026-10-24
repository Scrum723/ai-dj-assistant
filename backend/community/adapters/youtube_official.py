"""YouTube official liveChatMessages.list adapter with daily quota guard.

Docs (skimmed 2026-10-24):
- https://developers.google.com/youtube/data/live/reference/rest/v3/liveChatMessages/list
  GET liveChat/messages?liveChatId=&part=snippet,authorDetails
  Quota cost: 5 units per call. Response pollingIntervalMillis must be respected.
  nextPageToken for subsequent polls.
- Fallback: when daily quota exhausted, invoke on_quota_exhausted so pytchat
  YouTubeAdapter can take over.

Settings: community.adapters.youtube_official.{enabled, channel, live_chat_id,
  api_key, daily_quota}
Env: YOUTUBE_API_KEY
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import date
from typing import Any, Callable, Optional

from community.adapters.base import ChatAdapter, EmitFn
from community.schema import ChatMessage, ChatUser, SupportInfo

logger = logging.getLogger(__name__)

LIST_URL = "https://www.googleapis.com/youtube/v3/liveChat/messages"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
QUOTA_PER_LIST = 5
DEFAULT_DAILY_QUOTA = 10_000

_KIND_MAP = {
    "textMessageEvent": "chat",
    "superChatEvent": "super_chat",
    "superStickerEvent": "super_chat",
    "newSponsorEvent": "member",
    "memberMilestoneChatEvent": "member",
    "membershipGiftingEvent": "gift",
    "giftMembershipReceivedEvent": "gift",
}


def map_youtube_official_item(item: dict[str, Any], *, channel: str = "") -> Optional[ChatMessage]:
    """Map a liveChatMessage resource to ChatMessage."""
    if not isinstance(item, dict):
        return None
    msg_id = str(item.get("id") or "")
    snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
    author = item.get("authorDetails") if isinstance(item.get("authorDetails"), dict) else {}
    type_name = str(snippet.get("type") or "textMessageEvent")
    kind = _KIND_MAP.get(type_name, "chat")
    if type_name in {"chatEndedEvent", "messageDeletedEvent", "userBannedEvent", "tombstone"}:
        return None

    display = str(author.get("displayName") or "Fan").strip()[:40] or "Fan"
    uid = str(author.get("channelId") or snippet.get("authorChannelId") or display)
    text = str(snippet.get("displayMessage") or "")
    if type_name == "textMessageEvent":
        details = snippet.get("textMessageDetails") or {}
        if isinstance(details, dict) and details.get("messageText"):
            text = str(details["messageText"])

    support: Optional[SupportInfo] = None
    if type_name in {"superChatEvent", "superStickerEvent"}:
        details = snippet.get("superChatDetails") or snippet.get("superStickerDetails") or {}
        if isinstance(details, dict):
            micros = details.get("amountMicros")
            try:
                amount = float(micros) / 1_000_000.0 if micros is not None else None
            except (TypeError, ValueError):
                amount = None
            support = SupportInfo(
                amount=amount,
                currency=str(details.get("currency") or "") or None,
                tier=str(details["tier"]) if details.get("tier") is not None else None,
                count=1,
            )
            if details.get("userComment"):
                text = str(details["userComment"])
    elif type_name in {"newSponsorEvent", "memberMilestoneChatEvent"}:
        details = snippet.get("newSponsorDetails") or snippet.get("memberMilestoneChatDetails") or {}
        months = 1
        tier = None
        if isinstance(details, dict):
            if details.get("memberMonth") is not None:
                try:
                    months = int(details["memberMonth"])
                except (TypeError, ValueError):
                    months = 1
            tier = details.get("memberLevelName")
            if details.get("userComment"):
                text = str(details["userComment"])
        support = SupportInfo(tier=str(tier) if tier else None, count=months)
        if not text:
            text = "became a member" if type_name == "newSponsorEvent" else f"member {months} mo"
    elif type_name in {"membershipGiftingEvent", "giftMembershipReceivedEvent"}:
        details = snippet.get("membershipGiftingDetails") or {}
        count = 1
        if isinstance(details, dict) and details.get("giftMembershipsCount") is not None:
            try:
                count = max(1, int(details["giftMembershipsCount"]))
            except (TypeError, ValueError):
                count = 1
        support = SupportInfo(count=count)
        if not text:
            text = f"gifted {count} membership{'s' if count != 1 else ''}"

    if not msg_id:
        msg_id = f"{uid}:{snippet.get('publishedAt') or time.time()}"

    return ChatMessage(
        id=f"youtube:{msg_id}",
        platform="youtube",
        channel=channel or str(snippet.get("liveChatId") or ""),
        user=ChatUser(
            platform_user_id=uid,
            handle=display,
            display_name=display,
            avatar_url=str(author.get("profileImageUrl") or "") or None,
            badges=(
                (["moderator"] if author.get("isChatModerator") else [])
                + (["owner"] if author.get("isChatOwner") else [])
                + (["member"] if author.get("isChatSponsor") or kind in {"member", "gift"} else [])
            ),
            is_mod=bool(author.get("isChatModerator")),
            is_owner=bool(author.get("isChatOwner")),
        ),
        text=text,
        kind=kind,
        ts=time.time(),
        support=support,
        raw={"type": type_name, "id": msg_id},
    )


class YouTubeOfficialAdapter(ChatAdapter):
    name = "youtube_official"
    can_send = False

    def __init__(
        self,
        channel: str = "",
        *,
        api_key: str = "",
        live_chat_id: str = "",
        daily_quota: int = DEFAULT_DAILY_QUOTA,
        http_get=None,
        on_quota_exhausted: Optional[Callable[[], None]] = None,
    ):
        super().__init__(channel=channel)  # video id (optional if live_chat_id set)
        self.api_key = (api_key or os.getenv("YOUTUBE_API_KEY") or "").strip()
        self.live_chat_id = (live_chat_id or "").strip()
        self.daily_quota = max(QUOTA_PER_LIST, int(daily_quota or DEFAULT_DAILY_QUOTA))
        self._http_get = http_get
        self._on_quota_exhausted = on_quota_exhausted
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._page_token: Optional[str] = None
        self._poll_ms = 5000
        self._quota_used = 0
        self._quota_day = date.today().isoformat()
        self._quota_exhausted = False
        self._seen: set[str] = set()

    def configure(
        self,
        *,
        channel: Optional[str] = None,
        api_key: Optional[str] = None,
        live_chat_id: Optional[str] = None,
        daily_quota: Optional[int] = None,
        on_quota_exhausted: Optional[Callable[[], None]] = None,
    ) -> None:
        if channel is not None:
            self.set_channel(channel)
        if api_key is not None:
            self.api_key = api_key.strip()
        if live_chat_id is not None:
            self.live_chat_id = live_chat_id.strip()
        if daily_quota is not None:
            self.daily_quota = max(QUOTA_PER_LIST, int(daily_quota))
        if on_quota_exhausted is not None:
            self._on_quota_exhausted = on_quota_exhausted

    def is_ready(self) -> bool:
        return bool(self.api_key and (self.live_chat_id or self.channel))

    def quota_status(self) -> dict[str, Any]:
        self._roll_quota_day()
        return {
            "used": self._quota_used,
            "daily_quota": self.daily_quota,
            "remaining": max(0, self.daily_quota - self._quota_used),
            "exhausted": self._quota_exhausted,
            "day": self._quota_day,
            "cost_per_list": QUOTA_PER_LIST,
        }

    def _roll_quota_day(self) -> None:
        today = date.today().isoformat()
        if today != self._quota_day:
            self._quota_day = today
            self._quota_used = 0
            self._quota_exhausted = False

    def _charge_quota(self, units: int = QUOTA_PER_LIST) -> bool:
        self._roll_quota_day()
        if self._quota_used + units > self.daily_quota:
            self._quota_exhausted = True
            return False
        self._quota_used += units
        return True

    def start(self, emit: EmitFn) -> None:
        self.stop()
        self._emit = emit
        self._stop = threading.Event()
        self._running = True
        self._error = None
        self._page_token = None
        if not self.is_ready():
            self._connected = False
            self._error = "missing api_key or live_chat_id/video_id"
            self._running = False
            self._health = "dead"
            return
        if self._quota_exhausted:
            self._error = "daily quota exhausted"
            self._running = False
            self._health = "dead"
            self._trigger_fallback()
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="yt-official")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._running = False
        self._connected = False
        self._thread = None
        if not self._quota_exhausted:
            self._health = "idle"

    def _trigger_fallback(self) -> None:
        if self._on_quota_exhausted:
            try:
                self._on_quota_exhausted()
            except Exception:
                logger.exception("youtube_official fallback hook failed")

    def _get(self, url: str, params: dict) -> Any:
        if self._http_get:
            return self._http_get(url, params)
        import requests

        return requests.get(url, params=params, timeout=20)

    def _resolve_live_chat_id(self) -> Optional[str]:
        if self.live_chat_id:
            return self.live_chat_id
        if not self.channel or not self.api_key:
            return None
        if not self._charge_quota(1):  # videos.list costs 1
            return None
        res = self._get(
            VIDEOS_URL,
            {"part": "liveStreamingDetails", "id": self.channel, "key": self.api_key},
        )
        status = getattr(res, "status_code", 200)
        if status >= 400:
            self._error = f"videos.list HTTP {status}"
            return None
        data = res.json() if hasattr(res, "json") else res
        items = (data or {}).get("items") or []
        if not items:
            self._error = "no liveStreamingDetails for video"
            return None
        details = (items[0].get("liveStreamingDetails") or {}) if isinstance(items[0], dict) else {}
        chat_id = str(details.get("activeLiveChatId") or "").strip()
        if chat_id:
            self.live_chat_id = chat_id
        return chat_id or None

    def _loop(self) -> None:
        try:
            chat_id = self._resolve_live_chat_id()
            if not chat_id:
                if self._quota_exhausted:
                    self._error = "daily quota exhausted"
                    self._health = "dead"
                    self._trigger_fallback()
                else:
                    self._health = "dead"
                self._running = False
                return
            logger.info("YouTube official adapter polling liveChatId=%s", chat_id)
            while self._running and not self._stop.is_set():
                wait = self._backoff.remaining()
                if wait > 0:
                    self._stop.wait(min(wait, 5.0))
                    continue
                if not self._charge_quota(QUOTA_PER_LIST):
                    self._error = "daily quota exhausted"
                    self._quota_exhausted = True
                    self._connected = False
                    self._health = "dead"
                    self._trigger_fallback()
                    break
                params = {
                    "liveChatId": chat_id,
                    "part": "snippet,authorDetails",
                    "key": self.api_key,
                }
                if self._page_token:
                    params["pageToken"] = self._page_token
                try:
                    res = self._get(LIST_URL, params)
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
                        # dead adapter must not block others — just sleep and retry
                        self._stop.wait(max(5.0, self._poll_ms / 1000.0))
                        continue
                    data = res.json() if hasattr(res, "json") else res
                    if not isinstance(data, dict):
                        data = {}
                    self._backoff.on_success()
                    self._connected = True
                    self._error = None
                    self._health = "ok"
                    self._page_token = data.get("nextPageToken") or self._page_token
                    poll_ms = data.get("pollingIntervalMillis")
                    if poll_ms is not None:
                        try:
                            self._poll_ms = max(1000, int(poll_ms))
                        except (TypeError, ValueError):
                            pass
                    for item in data.get("items") or []:
                        msg = map_youtube_official_item(item, channel=self.channel or chat_id)
                        if not msg or msg.id in self._seen:
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
                    logger.exception("YouTube official poll failed")
                self._stop.wait(self._poll_ms / 1000.0)
        finally:
            self._connected = False
            self._running = False

    def status(self) -> dict[str, Any]:
        st = super().status()
        st["quota"] = self.quota_status()
        st["live_chat_id"] = self.live_chat_id or None
        return st
