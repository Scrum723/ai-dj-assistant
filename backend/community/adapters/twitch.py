"""Twitch chat adapter — anonymous read-only IRC over WebSocket.

Docs (skimmed 2026-10-24):
- https://dev.twitch.tv/docs/irc/ (IRC Concepts; wss://irc-ws.chat.twitch.tv:443 only —
  non-SSL WS decommissioned Aug 2025)
- https://dev.twitch.tv/docs/irc/tags (IRCv3 tags: display-name, badges, emotes, id, mod, user-id)

Official auth uses PASS oauth:<token> + NICK <login>. This adapter uses the
community justinfanXXXX anonymous nick for read-only JOIN (not in official auth
docs; EventSub is Twitch's preferred path). Follows plan: CAP REQ tags/commands,
PING/PONG, parse PRIVMSG tags.
"""

from __future__ import annotations

import logging
import random
import re
import threading
import time
from typing import Any, Optional
from urllib.parse import unquote

from community.adapters.base import ChatAdapter, EmitFn
from community.schema import ChatMessage, ChatUser, Emote, SupportInfo

logger = logging.getLogger(__name__)

IRC_WS_URL = "wss://irc-ws.chat.twitch.tv:443"
EMOTE_CDN = "https://static-cdn.jtvnw.net/emoticons/v2/{id}/default/dark/1.0"

_TAG_RE = re.compile(r"^@(\S+)\s+(.*)$")
_PRIVMSG_RE = re.compile(
    r"^:(\w+)!(\w+)@(\w+)\.tmi\.twitch\.tv\s+PRIVMSG\s+#(\w+)\s+:(.*)$"
)
_USERNOTICE_RE = re.compile(
    r"^:tmi\.twitch\.tv\s+USERNOTICE\s+#(\w+)(?:\s+:(.*))?$"
)
_SUB_MSG_IDS = frozenset(
    {
        "sub",
        "resub",
        "subgift",
        "anonsubgift",
        "submysterygift",
        "giftpaidupgrade",
        "primepaidupgrade",
        "anongiftpaidupgrade",
    }
)


def parse_tags(tag_str: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (tag_str or "").split(";"):
        if not part:
            continue
        if "=" in part:
            k, v = part.split("=", 1)
            out[k] = unquote(v.replace("\\:", ";").replace("\\s", " ").replace("\\\\", "\\"))
        else:
            out[part] = ""
    return out


def parse_emotes_tag(emotes: str, text: str) -> list[Emote]:
    """Parse Twitch emotes tag `id:start-end,start-end/id2:...` into Emote list."""
    result: list[Emote] = []
    if not emotes:
        return result
    for group in emotes.split("/"):
        if not group or ":" not in group:
            continue
        eid, ranges = group.split(":", 1)
        for rng in ranges.split(","):
            if "-" not in rng:
                continue
            a, b = rng.split("-", 1)
            try:
                start, end = int(a), int(b)
            except ValueError:
                continue
            name = text[start : end + 1] if 0 <= start <= end < len(text) else eid
            result.append(
                Emote(
                    name=name,
                    url=EMOTE_CDN.format(id=eid),
                    start=start,
                    end=end,
                )
            )
    return result


def _bits_support(tags: dict[str, str]) -> Optional[SupportInfo]:
    bits = tags.get("bits")
    if not bits:
        return None
    try:
        amount = float(bits)
    except (TypeError, ValueError):
        return None
    return SupportInfo(amount=amount, currency="bits", tier=None, count=1)


def parse_privmsg(raw_line: str) -> Optional[ChatMessage]:
    """Parse one IRC line (with optional tags) into ChatMessage. Testable, no network."""
    line = (raw_line or "").strip()
    if not line:
        return None
    tags: dict[str, str] = {}
    rest = line
    mtag = _TAG_RE.match(line)
    if mtag:
        tags = parse_tags(mtag.group(1))
        rest = mtag.group(2)

    m = _PRIVMSG_RE.match(rest)
    if not m:
        return None

    login = m.group(1)
    channel = m.group(4)
    text = m.group(5)
    msg_id = tags.get("id") or f"{login}:{time.time()}"
    display = tags.get("display-name") or login
    user_id = tags.get("user-id") or login
    badges_raw = tags.get("badges") or ""
    badge_names = [b.split("/", 1)[0] for b in badges_raw.split(",") if b]
    is_mod = tags.get("mod") == "1" or "moderator" in badge_names or "broadcaster" in badge_names
    is_owner = "broadcaster" in badge_names
    support = _bits_support(tags)
    kind = "super_chat" if support else "chat"

    return ChatMessage(
        id=f"twitch:{msg_id}",
        platform="twitch",
        channel=channel,
        user=ChatUser(
            platform_user_id=user_id,
            handle=login,
            display_name=display,
            badges=badge_names,
            is_mod=is_mod,
            is_owner=is_owner,
        ),
        text=text,
        emotes=parse_emotes_tag(tags.get("emotes") or "", text),
        kind=kind,
        ts=time.time(),
        support=support,
        raw={"tags": tags, "login": login},
    )


def parse_usernotice(raw_line: str) -> Optional[ChatMessage]:
    """Parse Twitch USERNOTICE (sub/resub/subgift). Testable, no network."""
    line = (raw_line or "").strip()
    if not line:
        return None
    tags: dict[str, str] = {}
    rest = line
    mtag = _TAG_RE.match(line)
    if mtag:
        tags = parse_tags(mtag.group(1))
        rest = mtag.group(2)

    m = _USERNOTICE_RE.match(rest)
    if not m:
        return None

    channel = m.group(1)
    text = (m.group(2) or "").strip()
    msg_id_tag = (tags.get("msg-id") or "").lower()
    login = tags.get("login") or tags.get("display-name") or "fan"
    display = tags.get("display-name") or login
    user_id = tags.get("user-id") or login
    msg_id = tags.get("id") or f"usernotice:{login}:{time.time()}"
    badges_raw = tags.get("badges") or ""
    badge_names = [b.split("/", 1)[0] for b in badges_raw.split(",") if b]

    kind = "chat"
    support: Optional[SupportInfo] = None
    if msg_id_tag in {"sub", "resub", "primepaidupgrade", "giftpaidupgrade", "anongiftpaidupgrade"}:
        kind = "member"
        months = tags.get("msg-param-cumulative-months") or tags.get("msg-param-months") or "1"
        try:
            months_i = int(months)
        except ValueError:
            months_i = 1
        plan = tags.get("msg-param-sub-plan") or tags.get("msg-param-sub-plan-name")
        support = SupportInfo(
            amount=None,
            currency=None,
            tier=str(plan) if plan else None,
            count=months_i,
        )
        if not text:
            text = f"{'re' if msg_id_tag == 'resub' else ''}subscribed"
    elif msg_id_tag in {"subgift", "anonsubgift", "submysterygift"}:
        kind = "gift"
        count_raw = tags.get("msg-param-mass-gift-count") or tags.get("msg-param-sender-count") or "1"
        try:
            count_i = max(1, int(count_raw))
        except ValueError:
            count_i = 1
        support = SupportInfo(count=count_i, tier=tags.get("msg-param-sub-plan") or None)
        recipient = tags.get("msg-param-recipient-display-name") or tags.get("msg-param-recipient-user-name")
        if not text:
            text = f"gifted a sub to {recipient}" if recipient else f"gifted {count_i} sub(s)"
    elif msg_id_tag == "raid":
        kind = "system"
    else:
        return None

    return ChatMessage(
        id=f"twitch:{msg_id}",
        platform="twitch",
        channel=channel,
        user=ChatUser(
            platform_user_id=user_id,
            handle=login,
            display_name=display,
            badges=badge_names,
            is_mod=tags.get("mod") == "1" or "moderator" in badge_names,
            is_owner="broadcaster" in badge_names,
        ),
        text=text,
        kind=kind,
        ts=time.time(),
        support=support,
        raw={"tags": tags, "login": login, "msg-id": msg_id_tag},
    )


def parse_irc_line(raw_line: str) -> Optional[ChatMessage]:
    """PRIVMSG or USERNOTICE → ChatMessage."""
    msg = parse_privmsg(raw_line)
    if msg:
        return msg
    return parse_usernotice(raw_line)


class TwitchAdapter(ChatAdapter):
    name = "twitch"
    can_send = False

    def __init__(self, channel: str = ""):
        super().__init__(channel=channel.lstrip("#").lower() if channel else "")
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._ws: Any = None

    def set_channel(self, channel: str) -> None:
        was_running = self._running and not self._stop.is_set()
        emit = self._emit
        super().set_channel((channel or "").lstrip("#").lower())
        if was_running and emit:
            self.start(emit)

    def start(self, emit: EmitFn) -> None:
        self.stop()
        self._emit = emit
        self._stop = threading.Event()
        self._running = True
        self._error = None
        if not self.channel:
            self._connected = False
            self._error = "no channel"
            self._running = False
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="twitch-irc")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._running = False
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        self._connected = False
        self._thread = None

    def _loop(self) -> None:
        nick = f"justinfan{random.randint(10000, 99999)}"
        channel = self.channel.lstrip("#").lower()
        try:
            from websockets.sync.client import connect
        except Exception as e:
            self._error = f"websockets missing: {e}"
            logger.error("websockets missing: %s", e)
            self._running = False
            return

        logger.info("Twitch adapter joining #%s as %s", channel, nick)
        try:
            with connect(IRC_WS_URL, close_timeout=5) as ws:
                self._ws = ws
                ws.send("CAP REQ :twitch.tv/tags twitch.tv/commands")
                ws.send(f"NICK {nick}")
                ws.send(f"JOIN #{channel}")
                self._connected = True
                while self._running and not self._stop.is_set():
                    try:
                        raw = ws.recv(timeout=1.0)
                    except TimeoutError:
                        continue
                    except Exception:
                        break
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", errors="replace")
                    for line in raw.split("\r\n"):
                        if not line:
                            continue
                        if line.startswith("PING"):
                            payload = line[5:].strip() or ":tmi.twitch.tv"
                            try:
                                ws.send(f"PONG {payload}")
                            except Exception:
                                return
                            continue
                        msg = parse_irc_line(line)
                        if msg:
                            self._note_emit(msg)
        except Exception as e:
            self._error = str(e)
            logger.exception("Twitch adapter died")
        finally:
            self._ws = None
            self._connected = False
            self._running = False
