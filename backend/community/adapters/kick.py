"""Kick chat adapter via official Events API webhooks + optional chat:write send.

Docs (skimmed 2026-10-24):
- https://docs.kick.com/events/webhook-security — Kick-Event-Signature = RSA-SHA256
  PKCS1v15 over `messageId.timestamp.rawBody`; fetch public key from
  https://api.kick.com/public/v1/public-key (do not hardcode; may rotate)
- https://docs.kick.com/events/event-types — chat.message.sent, channel.subscription.*,
  channel.followed, kicks.gifted
- https://docs.kick.com/apis/chat — POST /public/v1/chat (scope chat:write) → can_send

Webhook: POST /community/webhooks/kick with signature verification.
"""

from __future__ import annotations

import base64
import logging
import os
import time
from typing import Any, Optional

from community.adapters.base import ChatAdapter, EmitFn
from community.schema import ChatMessage, ChatUser, Emote, SupportInfo

logger = logging.getLogger(__name__)

KICK_PUBLIC_KEY_URL = "https://api.kick.com/public/v1/public-key"
KICK_CHAT_URL = "https://api.kick.com/public/v1/chat"

# Cached PEM from Kick (or test injection). Prefer live fetch per docs.
_cached_public_pem: Optional[str] = None


def set_kick_public_key_pem(pem: Optional[str]) -> None:
    """Inject PEM for tests / offline; None clears cache."""
    global _cached_public_pem
    _cached_public_pem = pem


def get_kick_public_key_pem(*, allow_network: bool = True) -> Optional[str]:
    """Return Kick public key PEM. Env KICK_PUBLIC_KEY_PEM overrides; else cache/fetch."""
    global _cached_public_pem
    env = (os.getenv("KICK_PUBLIC_KEY_PEM") or "").strip()
    if env:
        return env
    if _cached_public_pem:
        return _cached_public_pem
    if not allow_network:
        return None
    try:
        import requests

        res = requests.get(KICK_PUBLIC_KEY_URL, timeout=10)
        res.raise_for_status()
        data = res.json() if "application/json" in (res.headers.get("content-type") or "") else None
        pem = None
        if isinstance(data, dict):
            pem = data.get("data", {}).get("public_key") if isinstance(data.get("data"), dict) else data.get("public_key")
            if not pem and isinstance(data.get("data"), str):
                pem = data["data"]
        if not pem:
            pem = res.text
        pem = str(pem or "").strip()
        if "BEGIN PUBLIC KEY" in pem:
            _cached_public_pem = pem
            return pem
    except Exception:
        logger.exception("failed to fetch Kick public key")
    return _cached_public_pem


def verify_kick_signature(
    *,
    message_id: str,
    timestamp: str,
    body: bytes | str,
    signature_b64: str,
    public_pem: Optional[str] = None,
    allow_network: bool = True,
) -> bool:
    """Verify Kick-Event-Signature per docs (messageId.timestamp.body, RSA PKCS1v15 SHA256)."""
    pem = (public_pem or get_kick_public_key_pem(allow_network=allow_network) or "").strip()
    if not pem or not signature_b64:
        return False
    raw = body if isinstance(body, bytes) else body.encode("utf-8")
    signed = f"{message_id}.{timestamp}.".encode("utf-8") + raw
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError:
        logger.error("cryptography package required for Kick webhook verification")
        return False
    try:
        pub = serialization.load_pem_public_key(pem.encode("utf-8"))
        sig = base64.b64decode(signature_b64)
        pub.verify(sig, signed, padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception:
        logger.debug("Kick signature verification failed", exc_info=True)
        return False


def _parse_ts(value: Any) -> float:
    if value is None:
        return time.time()
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return time.time()
    try:
        from datetime import datetime

        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        try:
            return float(s)
        except ValueError:
            return time.time()


def _sender_user(sender: dict[str, Any]) -> ChatUser:
    handle = str(sender.get("username") or sender.get("channel_slug") or "Fan").strip()[:40] or "Fan"
    uid = str(sender.get("user_id") or sender.get("id") or handle)
    identity = sender.get("identity") if isinstance(sender.get("identity"), dict) else {}
    badges_raw = identity.get("badges") if isinstance(identity, dict) else []
    badge_names: list[str] = []
    if isinstance(badges_raw, list):
        for b in badges_raw:
            if isinstance(b, dict):
                badge_names.append(str(b.get("type") or b.get("text") or ""))
            elif b:
                badge_names.append(str(b))
    badge_names = [b for b in badge_names if b]
    is_mod = "moderator" in badge_names or "broadcaster" in badge_names
    return ChatUser(
        platform_user_id=uid,
        handle=handle,
        display_name=handle,
        avatar_url=str(sender.get("profile_picture") or "") or None,
        badges=badge_names,
        is_mod=is_mod,
        is_owner="broadcaster" in badge_names,
    )


def _emotes_from_kick(content: str, emotes: Any) -> list[Emote]:
    result: list[Emote] = []
    if not isinstance(emotes, list):
        return result
    for e in emotes:
        if not isinstance(e, dict):
            continue
        eid = str(e.get("emote_id") or "")
        positions = e.get("positions") or []
        if not isinstance(positions, list):
            continue
        for pos in positions:
            if not isinstance(pos, dict):
                continue
            try:
                start = int(pos.get("s", 0))
                end = int(pos.get("e", start))
            except (TypeError, ValueError):
                continue
            name = content[start : end + 1] if 0 <= start <= end <= len(content) else eid
            result.append(Emote(name=name or eid, url="", start=start, end=end))
    return result


def map_kick_webhook(
    payload: dict[str, Any],
    *,
    event_type: str = "",
    channel: str = "",
) -> Optional[ChatMessage]:
    """Map Kick Events API webhook body to ChatMessage."""
    if not isinstance(payload, dict):
        return None
    et = (event_type or payload.get("event") or payload.get("type") or "").strip().lower()
    broadcaster = payload.get("broadcaster") if isinstance(payload.get("broadcaster"), dict) else {}
    ch = channel or str(broadcaster.get("channel_slug") or broadcaster.get("username") or "")

    if et in {"chat.message.sent", "chat", ""} and payload.get("content") is not None:
        sender = payload.get("sender") if isinstance(payload.get("sender"), dict) else {}
        user = _sender_user(sender)
        content = str(payload.get("content") or "")
        msg_id = str(payload.get("message_id") or payload.get("id") or f"{user.platform_user_id}:{time.time()}")
        return ChatMessage(
            id=f"kick:{msg_id}",
            platform="kick",
            channel=ch,
            user=user,
            text=content,
            emotes=_emotes_from_kick(content, payload.get("emotes")),
            kind="chat",
            ts=_parse_ts(payload.get("created_at")),
            raw={"event": et or "chat.message.sent", "id": msg_id},
        )

    if et in {"channel.followed", "follow", "follower"}:
        follower = payload.get("follower") or payload.get("user") or payload.get("sender") or {}
        if not isinstance(follower, dict):
            follower = {"username": str(follower)}
        user = _sender_user(follower)
        msg_id = str(payload.get("id") or f"follow:{user.platform_user_id}:{time.time()}")
        return ChatMessage(
            id=f"kick:{msg_id}",
            platform="kick",
            channel=ch,
            user=user,
            text="followed",
            kind="follow",
            ts=_parse_ts(payload.get("created_at")),
            support=SupportInfo(count=1),
            raw={"event": et, "id": msg_id},
        )

    if et in {
        "channel.subscription.new",
        "channel.subscription.renewal",
        "channel.subscription.gifts",
        "subscription",
        "subscribed",
        "sub",
        "gift",
        "gifted_subs",
        "subscription_gift",
        "giftsubscription",
        "kicks.gifted",
    }:
        return map_kick_event({**payload, "type": et, "event": et}, channel=ch)

    # Fall through: try generic mapper
    return map_kick_event(payload, channel=ch)


def map_kick_event(event: dict[str, Any], channel: str = "") -> Optional[ChatMessage]:
    """Map Kick subscription / gift / chat fixtures (and webhook-shaped) to ChatMessage."""
    if not isinstance(event, dict):
        return None
    # Official chat.message.sent without type field
    if event.get("content") is not None and event.get("sender") and not (
        event.get("type") or event.get("event")
    ):
        return map_kick_webhook(event, event_type="chat.message.sent", channel=channel)

    etype = str(event.get("type") or event.get("event") or "chat").strip().lower()
    if etype in {"chat.message.sent"}:
        return map_kick_webhook(event, event_type=etype, channel=channel)

    user_raw = event.get("sender") or event.get("user") or event.get("gifter") or {}
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
    msg_id = str(event.get("id") or event.get("message_id") or f"{uid}:{time.time()}")
    text = str(event.get("content") or event.get("message") or event.get("text") or "")

    kind = "chat"
    support: Optional[SupportInfo] = None
    if etype in {
        "subscription",
        "subscribed",
        "sub",
        "channel.subscription.new",
        "channel.subscription.renewal",
    }:
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
    elif etype in {
        "gift",
        "gifted_subs",
        "subscription_gift",
        "giftsubscription",
        "channel.subscription.gifts",
    }:
        kind = "gift"
        gifted = event.get("gifted_usernames") or event.get("recipients") or []
        count = event.get("count") or event.get("gift_count") or event.get("giftees") or (
            len(gifted) if gifted else 1
        )
        try:
            count_i = int(count)
        except (TypeError, ValueError):
            count_i = 1
        support = SupportInfo(amount=None, currency=None, tier=None, count=max(1, count_i))
        if not text:
            text = f"gifted {count_i} sub{'s' if count_i != 1 else ''}"
    elif etype in {"kicks.gifted", "kicks"}:
        kind = "super_chat"
        amount = event.get("amount") or event.get("kicks") or event.get("gift_amount")
        try:
            amount_f = float(amount) if amount is not None else None
        except (TypeError, ValueError):
            amount_f = None
        support = SupportInfo(amount=amount_f, currency="kicks", count=1)
        if not text:
            text = f"gifted {int(amount_f) if amount_f else ''} kicks".strip()
    elif etype in {"follow", "follower", "channel.followed"}:
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
        ts=float(event.get("ts") or _parse_ts(event.get("created_at"))),
        support=support,
        raw={"type": etype, "id": msg_id},
    )


class KickAdapter(ChatAdapter):
    """Webhook-driven Kick reader; optional send via chat:write token."""

    name = "kick"
    can_send = False

    def __init__(
        self,
        channel: str = "",
        *,
        access_token: str = "",
        broadcaster_user_id: Optional[int] = None,
    ):
        super().__init__(channel=channel)
        self.access_token = (access_token or os.getenv("KICK_ACCESS_TOKEN") or "").strip()
        self.broadcaster_user_id = broadcaster_user_id
        self.can_send = bool(self.access_token)
        self._seen_ids: set[str] = set()

    def is_ready(self) -> bool:
        # Webhooks work once enabled; channel optional (filter/display).
        return True

    def configure(
        self,
        *,
        channel: Optional[str] = None,
        access_token: Optional[str] = None,
        broadcaster_user_id: Optional[int] = None,
    ) -> None:
        if channel is not None:
            self.set_channel(channel)
        if access_token is not None:
            self.access_token = access_token.strip()
            self.can_send = bool(self.access_token)
        if broadcaster_user_id is not None:
            self.broadcaster_user_id = broadcaster_user_id

    def start(self, emit: EmitFn) -> None:
        self._emit = emit
        self._running = True
        self._connected = True
        self._error = None
        self._health = "ok"

    def stop(self) -> None:
        self._running = False
        self._connected = False
        self._health = "idle"

    def handle_webhook_payload(
        self,
        payload: dict[str, Any],
        *,
        event_type: str = "",
    ) -> Optional[ChatMessage]:
        if not self._running or not self._emit:
            self._error = "adapter not started"
            return None
        msg = map_kick_webhook(payload, event_type=event_type, channel=self.channel)
        if not msg:
            return None
        if msg.id in self._seen_ids:
            return None
        self._seen_ids.add(msg.id)
        if len(self._seen_ids) > 5000:
            self._seen_ids = set(list(self._seen_ids)[-2500:])
        self._note_emit(msg)
        self._connected = True
        self._error = None
        self._health = "ok"
        return msg

    def send(self, text: str) -> dict:
        if not self.access_token:
            raise NotImplementedError("kick adapter cannot send without access_token")
        import requests

        body: dict[str, Any] = {"content": (text or "")[:500], "type": "bot"}
        if self.broadcaster_user_id is not None:
            body["broadcaster_user_id"] = int(self.broadcaster_user_id)
            body["type"] = "user"
        res = requests.post(
            KICK_CHAT_URL,
            headers={"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"},
            json=body,
            timeout=15,
        )
        if res.status_code == 429:
            self._backoff.on_error(is_429=True)
            self._error = "429 rate limited"
            raise RuntimeError("Kick chat send rate limited")
        res.raise_for_status()
        data = res.json() if hasattr(res, "json") else {}
        if isinstance(data, dict):
            mid = str((data.get("data") or {}).get("message_id") or data.get("id") or "")
        else:
            mid = ""
        return {"id": mid}
