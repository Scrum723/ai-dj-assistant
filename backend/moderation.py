"""Input/output moderation for Halo chat and the future community room.

check_input accepts either (author, message) or a ChatMessage-like dict
with author/message or user.handle + text.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any, Optional

# Mild default blocklist — operator extends via settings.moderation.blocklist
DEFAULT_BLOCKLIST = [
    "nigger",
    "nigga",
    "faggot",
    "retard",
    "kike",
    "tranny",
    "rape",
    "rapist",
]

LINK_RE = re.compile(
    r"(https?://\S+|www\.\S+|(?:[a-z0-9-]+\.)+(?:com|net|org|io|co|gg|tv|me|xyz|info)\b\S*)",
    re.I,
)
REPEAT_RE = re.compile(r"(.)\1{7,}")
WORD_RE = re.compile(r"[a-z0-9']+", re.I)

# ~9 tokens / 10 s → the 10th rapid message is throttled
_BUCKET_CAPACITY = 9.0
_BUCKET_REFILL_PER_SEC = 9.0 / 10.0

_lock = threading.Lock()
_buckets: dict[str, list[float]] = {}  # author_key -> [tokens, last_ts]
_muted: set[str] = set()


def _author_key(author: str) -> str:
    return (author or "").strip().lower()


def mute(name: str) -> None:
    key = _author_key(name)
    if key:
        with _lock:
            _muted.add(key)


def unmute(name: str) -> None:
    key = _author_key(name)
    with _lock:
        _muted.discard(key)


def is_muted(name: str) -> bool:
    return _author_key(name) in _muted


def muted_names() -> list[str]:
    with _lock:
        return sorted(_muted)


def reset_rate_limits() -> None:
    """Test helper — clear token buckets and mutes."""
    with _lock:
        _buckets.clear()
        _muted.clear()


def _blocklist_words(settings: Optional[dict] = None) -> list[str]:
    extra: list[str] = []
    if settings:
        mod = settings.get("moderation") or {}
        raw = mod.get("blocklist") or []
        if isinstance(raw, list):
            extra = [str(w).strip().lower() for w in raw if str(w).strip()]
    seen: set[str] = set()
    out: list[str] = []
    for w in DEFAULT_BLOCKLIST + extra:
        wl = w.lower()
        if wl not in seen:
            seen.add(wl)
            out.append(wl)
    return out


def _extract(author_or_msg: Any, message: Optional[str] = None) -> tuple[str, str]:
    if isinstance(author_or_msg, dict):
        msg = author_or_msg
        user = msg.get("user") if isinstance(msg.get("user"), dict) else {}
        author = (
            (user or {}).get("handle")
            or (user or {}).get("display_name")
            or msg.get("author")
            or ""
        )
        text = msg.get("text") if msg.get("text") is not None else msg.get("message")
        return str(author or ""), str(text or "")
    return str(author_or_msg or ""), str(message or "")


def _take_token(author: str) -> bool:
    """Return True if the author still has a token (allowed)."""
    key = _author_key(author) or "anon"
    now = time.monotonic()
    with _lock:
        tokens, last = _buckets.get(key, [_BUCKET_CAPACITY, now])
        elapsed = max(0.0, now - last)
        tokens = min(_BUCKET_CAPACITY, tokens + elapsed * _BUCKET_REFILL_PER_SEC)
        if tokens < 1.0:
            _buckets[key] = [tokens, now]
            return False
        _buckets[key] = [tokens - 1.0, now]
        return True


def check_input(
    author_or_msg: Any,
    message: Optional[str] = None,
    *,
    settings: Optional[dict] = None,
) -> tuple[bool, str]:
    """Return (ok, reason). reason is empty when ok."""
    author, text = _extract(author_or_msg, message)
    text = (text or "").strip()
    if not text:
        return False, "empty"

    if is_muted(author):
        return False, "muted"

    lower = text.lower()
    for word in _blocklist_words(settings):
        # whole-word-ish: blocklist term as substring with word boundaries when alphanumeric
        if re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", lower):
            return False, "blocklist"

    if LINK_RE.search(text):
        return False, "link"

    letters = [c for c in text if c.isalpha()]
    if len(letters) >= 8:
        caps = sum(1 for c in letters if c.isupper())
        if caps / len(letters) >= 0.7:
            return False, "caps"

    if REPEAT_RE.search(text):
        return False, "flood"

    if not _take_token(author):
        return False, "rate"

    return True, ""


def check_output(text: str, *, settings: Optional[dict] = None) -> Optional[str]:
    """Clean LLM output. Return None if it should not be spoken."""
    if text is None:
        return None
    cleaned = str(text).strip()
    if not cleaned:
        return None

    cleaned = LINK_RE.sub("", cleaned)
    cleaned = re.sub(r"[#*_`]+", "", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()

    lower = cleaned.lower()
    for word in _blocklist_words(settings):
        if re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", lower):
            return None

    if len(cleaned) < 3:
        return None
    return cleaned[:280]
