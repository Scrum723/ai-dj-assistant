"""YouTube live chat adapter via pytchat.

Docs (skimmed 2026-10-24):
- https://github.com/taizan-hokuto/pytchat
- https://github.com/taizan-hokuto/pytchat/wiki/LiveChat

Uses pytchat.create(video_id) + sync_items() — same behaviour as the former
cohost._start_youtube loop. TODO: official YouTube Data API liveChatMessages
(see adapters/youtube_official in a later phase) with quota guard.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

from community.adapters.base import ChatAdapter, EmitFn
from community.schema import ChatMessage, ChatUser, Emote, SupportInfo

logger = logging.getLogger(__name__)

_KIND_MAP = {
    "textMessage": "chat",
    "superChat": "super_chat",
    "superSticker": "super_chat",
    "newSponsor": "member",
    "memberMilestone": "member",
    "membershipGifting": "gift",
    "giftMembershipReceived": "gift",
}


def _support_from_pytchat(item: Any, kind: str) -> Optional[SupportInfo]:
    if kind not in {"super_chat", "member", "gift"}:
        return None
    amount = getattr(item, "amountValue", None)
    try:
        amount_f = float(amount) if amount is not None else None
    except (TypeError, ValueError):
        amount_f = None
    currency = getattr(item, "currency", None)
    currency_s = str(currency).strip() if currency else None
    tier = None
    # Official-API shaped attrs if present on fake/fixture objects
    details = getattr(item, "superChatDetails", None) or getattr(item, "memberMilestoneChatDetails", None)
    if isinstance(details, dict):
        if amount_f is None and details.get("amountMicros") is not None:
            try:
                amount_f = float(details["amountMicros"]) / 1_000_000.0
            except (TypeError, ValueError):
                pass
        if not currency_s and details.get("currency"):
            currency_s = str(details["currency"])
        if details.get("tier") is not None:
            tier = str(details["tier"])
        if details.get("memberLevelName"):
            tier = str(details["memberLevelName"])
        months = details.get("memberMonth")
        if months is not None and kind == "member":
            return SupportInfo(amount=amount_f, currency=currency_s, tier=tier, count=int(months))
    count = 1 if kind in {"super_chat", "member", "gift"} else None
    if amount_f is None and not currency_s and tier is None and kind == "chat":
        return None
    if kind == "chat":
        return None
    return SupportInfo(amount=amount_f, currency=currency_s, tier=tier, count=count)


def map_pytchat_item(item: Any, channel: str) -> Optional[ChatMessage]:
    """Map a pytchat chat item to ChatMessage. Pure helper for tests/fakes."""
    try:
        author = getattr(item, "author", None)
        name = (getattr(author, "name", None) or "Fan").strip()[:40] or "Fan"
        channel_id = str(getattr(author, "channelId", None) or name)
        msg_id = str(getattr(item, "id", None) or f"{channel_id}:{time.time()}")
        text = str(getattr(item, "message", None) or "")
        type_name = str(getattr(item, "type", None) or "textMessage")
        kind = _KIND_MAP.get(type_name, "chat")
        is_mod = bool(getattr(author, "isChatModerator", False))
        is_owner = bool(getattr(author, "isChatOwner", False))
        avatar = getattr(author, "imageUrl", None) or None
        badges: list[str] = []
        if is_mod:
            badges.append("moderator")
        if is_owner:
            badges.append("owner")
        if getattr(author, "isChatSponsor", False) or kind in {"member", "gift"}:
            badges.append("member")

        emotes: list[Emote] = []
        # messageEx may list emoji dicts; best-effort
        message_ex = getattr(item, "messageEx", None)
        if isinstance(message_ex, list):
            pos = 0
            for part in message_ex:
                if isinstance(part, dict) and part.get("txt"):
                    token = str(part.get("txt") or "")
                    url = str(part.get("url") or "")
                    start = pos
                    end = pos + max(0, len(token) - 1)
                    emotes.append(Emote(name=token.strip(":"), url=url, start=start, end=end))
                    pos = end + 1
                elif isinstance(part, str):
                    pos += len(part)

        support = _support_from_pytchat(item, kind)
        return ChatMessage(
            id=f"youtube:{msg_id}",
            platform="youtube",
            channel=channel or "",
            user=ChatUser(
                platform_user_id=channel_id,
                handle=name,
                display_name=name,
                avatar_url=avatar,
                badges=badges,
                is_mod=is_mod,
                is_owner=is_owner,
            ),
            text=text,
            emotes=emotes,
            kind=kind,
            ts=time.time(),
            support=support,
            raw={"type": type_name, "id": msg_id},
        )
    except Exception:
        logger.exception("map_pytchat_item failed")
        return None


class YouTubeAdapter(ChatAdapter):
    name = "youtube"
    can_send = False

    def __init__(self, channel: str = ""):
        super().__init__(channel=channel)
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self, emit: EmitFn) -> None:
        self.stop()
        self._emit = emit
        self._stop = threading.Event()
        self._running = True
        self._error = None
        if not self.channel:
            self._connected = False
            self._error = "no video id"
            self._running = False
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="yt-chat")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._running = False
        self._connected = False
        self._thread = None

    def set_channel(self, channel: str) -> None:
        was_running = self._running and not self._stop.is_set()
        emit = self._emit
        super().set_channel(channel)
        if was_running and emit:
            self.start(emit)

    def _loop(self) -> None:
        try:
            import pytchat
        except Exception as e:
            self._error = f"pytchat missing: {e}"
            logger.error("pytchat missing: %s", e)
            self._running = False
            return

        video_id = self.channel
        logger.info("YouTube adapter listening to live chat %s", video_id)
        try:
            chat = pytchat.create(video_id=video_id)
            self._connected = True
            while self._running and not self._stop.is_set() and chat.is_alive():
                for c in chat.get().sync_items():
                    if self._stop.is_set():
                        break
                    msg = map_pytchat_item(c, video_id)
                    if msg:
                        self._note_emit(msg)
                time.sleep(1)
        except Exception as e:
            self._error = str(e)
            logger.exception("YouTube adapter died")
        finally:
            self._connected = False
            self._running = False
