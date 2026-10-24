"""X (Twitter) experimental adapter — replies/quotes to a live post.

X has no public live-broadcast chat API. When API credentials and a post id
are set, this adapter polls recent search for conversation replies/quotes
and maps them as chat. Disabled by default; experimental.

Docs (skimmed 2026-10-24):
- https://docs.x.com/x-api/posts/search/quickstart/recent-search
- https://docs.x.com/x-api/fundamentals/rate-limits
  GET /2/tweets/search/recent — app bearer ~450/15min (tier-dependent; Basic lower)
- No live Spaces/broadcast chat endpoint for third parties

Settings: community.adapters.x.{enabled:false, post_id, bearer_token, can_post:false, poll_interval_sec}
Env: X_BEARER_TOKEN, X_POST_ID
can_send only when can_post is true (operator opts into posting tier).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlencode

from community.adapters.base import ChatAdapter, EmitFn
from community.adapters.experimental import AlertFn, DisableFn, ExperimentalMixin
from community.schema import ChatMessage, ChatUser

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.x.com/2/tweets/search/recent"
POST_URL = "https://api.x.com/2/tweets"


def _parse_ts(value: Any) -> float:
    if value is None:
        return time.time()
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    try:
        # 2024-01-15T10:30:00.000Z
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return time.time()


def map_x_tweet(
    tweet: dict[str, Any],
    *,
    users_by_id: Optional[dict[str, dict]] = None,
    post_id: str = "",
) -> Optional[ChatMessage]:
    """Map an X API v2 tweet object (+ optional includes.users) to ChatMessage."""
    if not isinstance(tweet, dict):
        return None
    tid = str(tweet.get("id") or "").strip()
    if not tid:
        return None
    text = str(tweet.get("text") or "")
    author_id = str(tweet.get("author_id") or "")
    users_by_id = users_by_id or {}
    user = users_by_id.get(author_id) or {}
    handle = str(user.get("username") or author_id or "Fan").strip()[:40] or "Fan"
    display = str(user.get("name") or handle)[:40]
    return ChatMessage(
        id=f"x:{tid}",
        platform="x",
        channel=post_id,
        user=ChatUser(
            platform_user_id=author_id or handle,
            handle=handle,
            display_name=display,
        ),
        text=text,
        kind="chat",
        ts=_parse_ts(tweet.get("created_at")),
        raw={"id": tid, "author_id": author_id},
    )


def extract_x_search_messages(payload: dict[str, Any], *, post_id: str = "") -> list[ChatMessage]:
    """Pull tweets from a recent-search JSON payload."""
    out: list[ChatMessage] = []
    if not isinstance(payload, dict):
        return out
    includes = payload.get("includes") if isinstance(payload.get("includes"), dict) else {}
    users_list = includes.get("users") or []
    users_by_id: dict[str, dict] = {}
    if isinstance(users_list, list):
        for u in users_list:
            if isinstance(u, dict) and u.get("id"):
                users_by_id[str(u["id"])] = u
    for tweet in payload.get("data") or []:
        msg = map_x_tweet(tweet, users_by_id=users_by_id, post_id=post_id)
        if msg:
            out.append(msg)
    return out


class XAdapter(ChatAdapter, ExperimentalMixin):
    name = "x"
    can_send = False
    experimental = True

    def __init__(
        self,
        channel: str = "",
        *,
        post_id: str = "",
        bearer_token: str = "",
        can_post: bool = False,
        poll_interval_sec: float = 15.0,
        on_auto_disable: Optional[DisableFn] = None,
        on_alert: Optional[AlertFn] = None,
        http_get=None,
        http_post=None,
    ):
        super().__init__(channel=channel or post_id)
        self._init_experimental(on_auto_disable=on_auto_disable, on_alert=on_alert)
        self.post_id = (post_id or channel or os.getenv("X_POST_ID") or "").strip()
        if self.post_id:
            self.channel = self.post_id
        self.bearer_token = (bearer_token or os.getenv("X_BEARER_TOKEN") or "").strip()
        self.can_post = bool(can_post)
        self.can_send = bool(can_post)
        self.poll_interval_sec = max(5.0, float(poll_interval_sec))
        self._http_get = http_get
        self._http_post = http_post
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._seen: set[str] = set()
        self._since_id: Optional[str] = None

    def configure(
        self,
        *,
        post_id: Optional[str] = None,
        channel: Optional[str] = None,
        bearer_token: Optional[str] = None,
        can_post: Optional[bool] = None,
        poll_interval_sec: Optional[float] = None,
    ) -> None:
        pid = post_id if post_id is not None else channel
        if pid is not None:
            self.post_id = str(pid).strip()
            self.channel = self.post_id
        if bearer_token is not None:
            self.bearer_token = bearer_token.strip()
        if can_post is not None:
            self.can_post = bool(can_post)
            self.can_send = bool(can_post)
        if poll_interval_sec is not None:
            self.poll_interval_sec = max(5.0, float(poll_interval_sec))

    def is_ready(self) -> bool:
        if self._auto_disabled:
            return False
        return bool(self.post_id and self.bearer_token)

    def start(self, emit: EmitFn) -> None:
        self.stop()
        self._emit = emit
        self._stop = threading.Event()
        self._running = True
        self._error = None
        if self._auto_disabled:
            self._connected = False
            self._error = "auto-disabled after repeated failures"
            self._running = False
            self._health = "dead"
            return
        if not self.is_ready():
            self._connected = False
            self._error = "missing X post_id or bearer_token"
            self._running = False
            self._health = "dead"
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="x-replies")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._running = False
        self._connected = False
        self._thread = None
        self._health = "idle"

    def send(self, text: str) -> None:
        if not self.can_send:
            raise NotImplementedError("x adapter cannot send (can_post=false)")
        body = {"text": (text or "").strip()[:280]}
        if self.post_id:
            body["reply"] = {"in_reply_to_tweet_id": self.post_id}
        if self._http_post:
            res = self._http_post(POST_URL, body, self._headers())
        else:
            import requests

            res = requests.post(POST_URL, json=body, headers=self._headers(), timeout=15)
        status = getattr(res, "status_code", 200)
        if status >= 400:
            raise RuntimeError(f"X post failed HTTP {status}")

    def status(self) -> dict[str, Any]:
        st = super().status()
        st.update(self.experimental_status())
        st["post_id"] = self.post_id
        st["can_post"] = bool(self.can_post)
        return st

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.bearer_token}"}

    def _get(self, url: str) -> Any:
        if self._http_get:
            return self._http_get(url)
        import requests

        return requests.get(url, headers=self._headers(), timeout=15)

    def _search_url(self) -> str:
        # conversation_id matches the root post; also catch quotes of it
        q = f"conversation_id:{self.post_id} OR quoted_tweet_id:{self.post_id}"
        params = {
            "query": q,
            "max_results": "50",
            "tweet.fields": "created_at,author_id,conversation_id",
            "expansions": "author_id",
            "user.fields": "username,name",
        }
        if self._since_id:
            params["since_id"] = self._since_id
        return f"{SEARCH_URL}?{urlencode(params)}"

    def _loop(self) -> None:
        logger.info("X experimental adapter polling replies for post %s", self.post_id)
        while self._running and not self._stop.is_set():
            wait = self._backoff.remaining()
            if wait > 0:
                self._stop.wait(min(wait, 5.0))
                continue
            try:
                res = self._get(self._search_url())
                status = getattr(res, "status_code", 200)
                if status == 429:
                    delay = self._backoff.on_error(is_429=True)
                    self._error = f"429 backoff {delay:.1f}s"
                    self._health = "degraded"
                    if self._note_failure(self._error):
                        return
                    self._stop.wait(min(delay, self.poll_interval_sec))
                    continue
                if status >= 400:
                    body = getattr(res, "text", "")[:200]
                    self._backoff.on_error()
                    self._error = f"HTTP {status}: {body}"
                    self._connected = False
                    self._health = "dead"
                    if self._note_failure(self._error):
                        return
                    self._stop.wait(self.poll_interval_sec)
                    continue
                data = res.json() if hasattr(res, "json") else res
                self._backoff.on_success()
                self._note_success()
                self._connected = True
                self._error = None
                self._health = "ok"
                newest = None
                for msg in extract_x_search_messages(
                    data if isinstance(data, dict) else {}, post_id=self.post_id
                ):
                    if msg.id in self._seen:
                        continue
                    # Skip the root post itself if it appears
                    if msg.id == f"x:{self.post_id}":
                        continue
                    self._seen.add(msg.id)
                    tid = msg.id.split(":", 1)[-1]
                    if newest is None or tid > newest:
                        newest = tid
                    self._note_emit(msg)
                if newest:
                    self._since_id = newest
                if len(self._seen) > 5000:
                    self._seen = set(list(self._seen)[-2500:])
            except Exception as e:
                self._backoff.on_error()
                self._error = str(e)
                self._connected = False
                self._health = "dead"
                logger.exception("X adapter poll failed")
                if self._note_failure(str(e)):
                    return
            self._stop.wait(self.poll_interval_sec)
        self._connected = False
        self._running = False
