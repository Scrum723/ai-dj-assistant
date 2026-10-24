"""TikTok LIVE gift mappers (experimental fixtures). Full live adapter is B5."""

from __future__ import annotations

import time
from typing import Any, Optional

from community.schema import ChatMessage, ChatUser, SupportInfo


def map_tiktok_event(event: dict[str, Any], channel: str = "") -> Optional[ChatMessage]:
    """Map TikTok gift / follow / chat fixtures to ChatMessage."""
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
    elif etype in {"follow", "member", "social"}:
        action = str(event.get("action") or etype).lower()
        if "follow" in action or etype == "follow":
            kind = "follow"
            support = SupportInfo(count=1)
            if not text:
                text = "followed"
        else:
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
