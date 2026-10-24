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

KINDS = ("chat", "super_chat", "gift", "follow", "system")


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
    raw: Optional[dict[str, Any]] = None

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
        return {
            "author": user.display_name or user.handle or "Fan",
            "message": self.text,
            "platform": self.platform,
            "ts": self.ts,
            "id": self.id,
            "kind": self.kind,
        }

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
            raw=data.get("raw"),
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
