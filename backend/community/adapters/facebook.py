"""Facebook Live comments via Graph API (Page).

Docs (skimmed 2026-10-24):
- https://developers.facebook.com/docs/graph-api/reference/live-video/comments/
  GET /v26.0/{live-video-id}/comments — Page access token; filter=stream|toplevel
- https://developers.facebook.com/docs/live-video-api/interact-with-viewers/
  Poll comments every few seconds, or SSE at streaming-graph.facebook.com
  (this adapter polls; respects rate limits via exponential backoff on 429)

Settings: community.adapters.facebook.{enabled, live_video_id, page_access_token, poll_interval_sec}
Env: FACEBOOK_PAGE_ACCESS_TOKEN
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Optional

from community.adapters.base import ChatAdapter, EmitFn
from community.schema import ChatMessage, ChatUser

logger = logging.getLogger(__name__)

GRAPH_VERSION = "v26.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"


def _parse_ts(value: Any) -> float:
    if value is None:
        return time.time()
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    try:
        from datetime import datetime

        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s.replace("+0000", "+00:00")).timestamp()
    except Exception:
        return time.time()


def map_facebook_comment(item: dict[str, Any], *, live_video_id: str = "") -> Optional[ChatMessage]:
    """Map a Graph API Comment node to ChatMessage."""
    if not isinstance(item, dict):
        return None
    msg_id = str(item.get("id") or "")
    if not msg_id:
        return None
    from_user = item.get("from") if isinstance(item.get("from"), dict) else {}
    name = str(from_user.get("name") or "Fan").strip()[:40] or "Fan"
    uid = str(from_user.get("id") or name)
    text = str(item.get("message") or "")
    return ChatMessage(
        id=f"facebook:{msg_id}",
        platform="facebook",
        channel=live_video_id or "",
        user=ChatUser(platform_user_id=uid, handle=name, display_name=name),
        text=text,
        kind="chat",
        ts=_parse_ts(item.get("created_time")),
        raw={"id": msg_id},
    )


class FacebookAdapter(ChatAdapter):
    name = "facebook"
    can_send = False

    def __init__(
        self,
        channel: str = "",
        *,
        page_access_token: str = "",
        poll_interval_sec: float = 3.0,
        http_get=None,
    ):
        super().__init__(channel=channel)  # channel = live_video_id
        self.page_access_token = (
            page_access_token or os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN") or ""
        ).strip()
        self.poll_interval_sec = max(1.0, float(poll_interval_sec))
        self._http_get = http_get  # injectable for tests
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._seen: set[str] = set()

    def configure(
        self,
        *,
        live_video_id: Optional[str] = None,
        page_access_token: Optional[str] = None,
        poll_interval_sec: Optional[float] = None,
    ) -> None:
        if live_video_id is not None:
            self.set_channel(live_video_id)
        if page_access_token is not None:
            self.page_access_token = page_access_token.strip()
        if poll_interval_sec is not None:
            self.poll_interval_sec = max(1.0, float(poll_interval_sec))

    def is_ready(self) -> bool:
        return bool(self.channel and self.page_access_token)

    def start(self, emit: EmitFn) -> None:
        self.stop()
        self._emit = emit
        self._stop = threading.Event()
        self._running = True
        self._error = None
        if not self.is_ready():
            self._connected = False
            self._error = "missing live_video_id or page_access_token"
            self._running = False
            self._health = "dead"
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="fb-live-comments")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._running = False
        self._connected = False
        self._thread = None
        self._health = "idle"

    def _get(self, url: str, params: dict) -> Any:
        if self._http_get:
            return self._http_get(url, params)
        import requests

        res = requests.get(url, params=params, timeout=15)
        return res

    def _loop(self) -> None:
        vid = self.channel
        url = f"{GRAPH_BASE}/{vid}/comments"
        logger.info("Facebook adapter polling live comments for %s", vid)
        while self._running and not self._stop.is_set():
            wait = self._backoff.remaining()
            if wait > 0:
                self._stop.wait(min(wait, 5.0))
                continue
            try:
                res = self._get(
                    url,
                    {
                        "access_token": self.page_access_token,
                        "filter": "toplevel",
                        "live_filter": "no_filter",
                        "order": "chronological",
                        "limit": 50,
                    },
                )
                status = getattr(res, "status_code", 200)
                if status == 429:
                    delay = self._backoff.on_error(is_429=True)
                    self._error = f"429 backoff {delay:.1f}s"
                    self._health = "degraded"
                    self._stop.wait(min(delay, 5.0))
                    continue
                if status >= 400:
                    body = getattr(res, "text", "")[:200]
                    self._backoff.on_error()
                    self._error = f"HTTP {status}: {body}"
                    self._connected = False
                    self._health = "dead"
                    self._stop.wait(self.poll_interval_sec)
                    continue
                data = res.json() if hasattr(res, "json") else res
                if not isinstance(data, dict):
                    data = {}
                items = data.get("data") or []
                self._backoff.on_success()
                self._connected = True
                self._error = None
                self._health = "ok"
                for item in items:
                    msg = map_facebook_comment(item, live_video_id=vid)
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
                logger.exception("Facebook adapter poll failed")
            self._stop.wait(self.poll_interval_sec)
        self._connected = False
        self._running = False
