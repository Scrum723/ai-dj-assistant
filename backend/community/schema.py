"""Unified ChatMessage schema for the community room."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
import time


PLATFORMS = (
    "youtube",
    "twitch",
    "kick",
    "tiktok",
    "x",
    "facebook",
    "rumble",
    "room",
    "halo",
)

KINDS = ("chat", "super_chat", "gift", "member", "follow", "system")

SUPPORT_KINDS = frozenset({"super_chat", "gift", "member", "follow"})


@dataclass
class SupportInfo:
    """Paid / membership / gift metadata. Unsupported fields stay null."""

    amount: Optional[float] = None
    currency: Optional[str] = None
    tier: Optional[str] = None
    count: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "amount": self.amount,
            "currency": self.currency,
            "tier": self.tier,
            "count": self.count,
        }

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> Optional["SupportInfo"]:
        if not data or not isinstance(data, dict):
            return None
        amount = data.get("amount")
        try:
            amount_f = float(amount) if amount is not None else None
        except (TypeError, ValueError):
            amount_f = None
        count = data.get("count")
        try:
            count_i = int(count) if count is not None else None
        except (TypeError, ValueError):
            count_i = None
        currency = data.get("currency")
        tier = data.get("tier")
        if amount_f is None and currency is None and tier is None and count_i is None:
            return None
        return cls(
            amount=amount_f,
            currency=str(currency) if currency is not None else None,
            tier=str(tier) if tier is not None else None,
            count=count_i,
        )


@dataclass
class ChatUser:
    platform_user_id: str = ""
    handle: str = ""
    display_name: str = ""
    avatar_url: Optional[str] = None
    badges: list[str] = field(default_factory=list)
    is_mod: bool = False
    is_owner: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform_user_id": self.platform_user_id,
            "handle": self.handle,
            "display_name": self.display_name,
            "avatar_url": self.avatar_url,
            "badges": list(self.badges),
            "is_mod": self.is_mod,
            "is_owner": self.is_owner,
        }


@dataclass
class Emote:
    name: str
    url: str = ""
    start: int = 0
    end: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "url": self.url, "start": self.start, "end": self.end}


@dataclass
class ChatMessage:
    id: str
    platform: str
    channel: str
    user: ChatUser
    text: str
    emotes: list[Emote] = field(default_factory=list)
    kind: str = "chat"
    ts: float = field(default_factory=time.time)
    profile_id: Optional[int] = None
    support: Optional[SupportInfo] = None
    raw: Optional[dict[str, Any]] = None
    visits: Optional[int] = None
    streak: Optional[int] = None

    def to_dict(self, *, include_raw: bool = True) -> dict[str, Any]:
        data = {
            "id": self.id,
            "platform": self.platform,
            "channel": self.channel,
            "user": self.user.to_dict() if isinstance(self.user, ChatUser) else self.user,
            "profile_id": self.profile_id,
            "text": self.text,
            "emotes": [
                e.to_dict() if isinstance(e, Emote) else e for e in (self.emotes or [])
            ],
            "kind": self.kind,
            "ts": self.ts,
            "support": self.support.to_dict() if self.support else None,
            "visits": self.visits,
            "streak": self.streak,
        }
        if include_raw and self.raw is not None:
            data["raw"] = self.raw
        return data

    def to_client(self) -> dict[str, Any]:
        """Client payload — never includes raw."""
        return self.to_dict(include_raw=False)

    def to_legacy(self) -> dict[str, Any]:
        """Overlay / cohost chat line shape."""
        user = self.user if isinstance(self.user, ChatUser) else ChatUser()
        out = {
            "author": user.display_name or user.handle or "Fan",
            "message": self.text,
            "platform": self.platform,
            "ts": self.ts,
            "id": self.id,
            "kind": self.kind,
        }
        if self.support:
            out["support"] = self.support.to_dict()
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChatMessage":
        user_raw = data.get("user") or {}
        if isinstance(user_raw, ChatUser):
            user = user_raw
        else:
            user = ChatUser(
                platform_user_id=str(user_raw.get("platform_user_id") or ""),
                handle=str(user_raw.get("handle") or ""),
                display_name=str(user_raw.get("display_name") or ""),
                avatar_url=user_raw.get("avatar_url"),
                badges=list(user_raw.get("badges") or []),
                is_mod=bool(user_raw.get("is_mod")),
                is_owner=bool(user_raw.get("is_owner")),
            )
        emotes_raw = data.get("emotes") or []
        emotes: list[Emote] = []
        for e in emotes_raw:
            if isinstance(e, Emote):
                emotes.append(e)
            else:
                emotes.append(
                    Emote(
                        name=str(e.get("name") or ""),
                        url=str(e.get("url") or ""),
                        start=int(e.get("start") or 0),
                        end=int(e.get("end") or 0),
                    )
                )
        support_raw = data.get("support")
        if isinstance(support_raw, SupportInfo):
            support = support_raw
        else:
            support = SupportInfo.from_dict(support_raw)
        return cls(
            id=str(data.get("id") or ""),
            platform=str(data.get("platform") or "room"),
            channel=str(data.get("channel") or ""),
            user=user,
            text=str(data.get("text") or ""),
            emotes=emotes,
            kind=str(data.get("kind") or "chat"),
            ts=float(data.get("ts") or time.time()),
            profile_id=data.get("profile_id"),
            support=support,
            raw=data.get("raw"),
            visits=int(data["visits"]) if data.get("visits") is not None else None,
            streak=int(data["streak"]) if data.get("streak") is not None else None,
        )

    @classmethod
    def make_room(
        cls,
        *,
        author: str,
        text: str,
        channel: str = "booth",
        msg_id: Optional[str] = None,
    ) -> "ChatMessage":
        handle = (author or "Fan").strip()[:40] or "Fan"
        ts = time.time()
        mid = msg_id or f"room:{handle}:{ts}"
        return cls(
            id=mid if mid.startswith("room:") else f"room:{mid}",
            platform="room",
            channel=channel,
            user=ChatUser(
                platform_user_id=handle.lower(),
                handle=handle,
                display_name=handle,
            ),
            text=(text or "").strip()[:280],
            kind="chat",
            ts=ts,
        )
