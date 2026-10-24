"""Kick chat event mappers (fixture / experimental). Full live adapter is B4."""

from __future__ import annotations

import time
from typing import Any, Optional

from community.schema import ChatMessage, ChatUser, SupportInfo


def map_kick_event(event: dict[str, Any], channel: str = "") -> Optional[ChatMessage]:
    """Map Kick subscription / gift / chat fixtures to ChatMessage."""
    if not isinstance(event, dict):
        return None
    etype = str(event.get("type") or event.get("event") or "chat").strip().lower()
    user_raw = event.get("sender") or event.get("user") or {}
    if not isinstance(user_raw, dict):
        user_raw = {"username": str(user_raw)}
    handle = str(
        user_raw.get("username")
        or user_raw.get("slug")
        or user_raw.get("handle")
        or "Fan"
    ).strip()[:40] or "Fan"
    display = str(user_raw.get("display_name") or user_raw.get("name") or handle)[:40]
    uid = str(user_raw.get("id") or user_raw.get("user_id") or handle)
    msg_id = str(event.get("id") or f"{uid}:{time.time()}")
    text = str(event.get("content") or event.get("message") or event.get("text") or "")

    kind = "chat"
    support: Optional[SupportInfo] = None
    if etype in {"subscription", "subscribed", "sub"}:
        kind = "member"
        months = event.get("months") or event.get("duration") or 1
        try:
            months_i = int(months)
        except (TypeError, ValueError):
            months_i = 1
        tier = event.get("tier") or event.get("sub_tier")
        support = SupportInfo(
            amount=None,
            currency=None,
            tier=str(tier) if tier is not None else None,
            count=months_i,
        )
        if not text:
            text = f"subscribed for {months_i} month{'s' if months_i != 1 else ''}"
    elif etype in {"gift", "gifted_subs", "subscription_gift", "giftsubscription"}:
        kind = "gift"
        gifted = event.get("gifted_usernames") or event.get("recipients") or []
        count = event.get("count") or event.get("gift_count") or (len(gifted) if gifted else 1)
        try:
            count_i = int(count)
        except (TypeError, ValueError):
            count_i = 1
        support = SupportInfo(amount=None, currency=None, tier=None, count=max(1, count_i))
        if not text:
            text = f"gifted {count_i} sub{'s' if count_i != 1 else ''}"
    elif etype in {"follow", "follower"}:
        kind = "follow"
        support = SupportInfo(count=1)
        if not text:
            text = "followed"

    return ChatMessage(
        id=f"kick:{msg_id}",
        platform="kick",
        channel=channel or str(event.get("channel") or ""),
        user=ChatUser(
            platform_user_id=uid,
            handle=handle,
            display_name=display,
            badges=["subscriber"] if kind in {"member", "gift"} else [],
        ),
        text=text,
        kind=kind,
        ts=float(event.get("ts") or time.time()),
        support=support,
        raw={"type": etype, "id": msg_id},
    )
