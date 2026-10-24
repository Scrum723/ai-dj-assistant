"""SQLite persistence for community messages and presence."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from community.schema import ChatMessage, ChatUser, Emote

logger = logging.getLogger(__name__)

PRESENCE_TTL_SEC = 5 * 60
DEFAULT_RETENTION_DAYS = 30


def default_data_dir() -> Path:
    raw = os.getenv("DATA_DIR")
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[1]


class CommunityStore:
    def __init__(
        self,
        db_path: Optional[os.PathLike | str] = None,
        *,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        clock=None,
    ):
        if db_path is None:
            data_dir = default_data_dir()
            data_dir.mkdir(parents=True, exist_ok=True)
            db_path = data_dir / "community.db"
        else:
            db_path = Path(db_path)
            db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = Path(db_path)
        self.retention_days = max(1, int(retention_days))
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self._init_db()

    def _now(self) -> float:
        return float(self._clock())

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    channel TEXT NOT NULL DEFAULT '',
                    handle TEXT NOT NULL DEFAULT '',
                    profile_id TEXT,
                    text TEXT NOT NULL DEFAULT '',
                    emotes_json TEXT NOT NULL DEFAULT '[]',
                    kind TEXT NOT NULL DEFAULT 'chat',
                    ts REAL NOT NULL,
                    display_name TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts DESC)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS presence (
                    platform TEXT NOT NULL,
                    handle TEXT NOT NULL,
                    last_seen REAL NOT NULL,
                    PRIMARY KEY (platform, handle)
                )
                """
            )
            conn.commit()

    def insert_message(self, msg: ChatMessage) -> None:
        user = msg.user if isinstance(msg.user, ChatUser) else ChatUser()
        handle = (user.handle or user.display_name or "").strip()
        display = (user.display_name or user.handle or "").strip()
        emotes = [
            e.to_dict() if isinstance(e, Emote) else e for e in (msg.emotes or [])
        ]
        profile_id = None if msg.profile_id is None else str(msg.profile_id)
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO messages
                    (id, platform, channel, handle, profile_id, text, emotes_json, kind, ts, display_name)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        msg.id,
                        msg.platform,
                        msg.channel or "",
                        handle,
                        profile_id,
                        msg.text or "",
                        json.dumps(emotes),
                        msg.kind or "chat",
                        float(msg.ts),
                        display,
                    ),
                )
                conn.commit()
        if handle:
            self.touch_presence(msg.platform, handle)

    def history(
        self,
        *,
        before: Optional[float] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 100), 200))
        params: list[Any] = []
        where = ""
        if before is not None:
            where = "WHERE ts < ?"
            params.append(float(before))
        params.append(limit)
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    f"""
                    SELECT id, platform, channel, handle, profile_id, text, emotes_json, kind, ts, display_name
                    FROM messages
                    {where}
                    ORDER BY ts DESC
                    LIMIT ?
                    """,
                    params,
                ).fetchall()
        # Return chronological (oldest → newest) for UI
        items = [self._row_to_client(r) for r in reversed(rows)]
        return items

    def _row_to_client(self, row: sqlite3.Row) -> dict[str, Any]:
        try:
            emotes = json.loads(row["emotes_json"] or "[]")
        except Exception:
            emotes = []
        handle = row["handle"] or ""
        display = row["display_name"] or handle
        profile_id = row["profile_id"]
        if profile_id is not None:
            try:
                profile_id = int(profile_id)
            except (TypeError, ValueError):
                pass
        return {
            "id": row["id"],
            "platform": row["platform"],
            "channel": row["channel"] or "",
            "user": {
                "platform_user_id": handle.lower() if handle else "",
                "handle": handle,
                "display_name": display,
                "avatar_url": None,
                "badges": [],
                "is_mod": False,
                "is_owner": False,
            },
            "profile_id": profile_id,
            "text": row["text"] or "",
            "emotes": emotes,
            "kind": row["kind"] or "chat",
            "ts": float(row["ts"]),
        }

    def prune(self, *, retention_days: Optional[int] = None) -> int:
        days = self.retention_days if retention_days is None else max(1, int(retention_days))
        cutoff = self._now() - (days * 86400)
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute("DELETE FROM messages WHERE ts < ?", (cutoff,))
                conn.commit()
                return int(cur.rowcount or 0)

    def touch_presence(self, platform: str, handle: str, *, at: Optional[float] = None) -> None:
        platform = (platform or "room").strip().lower() or "room"
        handle = (handle or "").strip()
        if not handle:
            return
        ts = self._now() if at is None else float(at)
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO presence (platform, handle, last_seen)
                    VALUES (?, ?, ?)
                    ON CONFLICT(platform, handle) DO UPDATE SET last_seen = excluded.last_seen
                    """,
                    (platform, handle, ts),
                )
                conn.commit()

    def presence(
        self,
        *,
        ttl_sec: float = PRESENCE_TTL_SEC,
        room_sockets: int = 0,
        now: Optional[float] = None,
    ) -> dict[str, Any]:
        """Active users in the last ttl_sec; room count uses connected sockets."""
        ts = self._now() if now is None else float(now)
        cutoff = ts - float(ttl_sec)
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT platform, handle, last_seen FROM presence
                    WHERE last_seen >= ?
                    ORDER BY last_seen DESC
                    """,
                    (cutoff,),
                ).fetchall()
        users = [
            {
                "platform": r["platform"],
                "handle": r["handle"],
                "last_seen": float(r["last_seen"]),
            }
            for r in rows
            if r["platform"] != "room"
        ]
        counts: dict[str, int] = {}
        for u in users:
            counts[u["platform"]] = counts.get(u["platform"], 0) + 1
        counts["room"] = max(0, int(room_sockets))
        return {
            "ts": ts,
            "ttl_sec": float(ttl_sec),
            "counts": counts,
            "users": users,
            "total": sum(counts.values()),
        }
