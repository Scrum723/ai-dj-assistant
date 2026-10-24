"""Per-viewer memory for Halo — SQLite keyed by platform+handle."""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def default_data_dir() -> Path:
    raw = os.getenv("DATA_DIR")
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parent


def _day_key(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def ordinal(n: int) -> str:
    n = int(n)
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


@dataclass
class ViewerRecord:
    platform: str
    handle: str
    profile_id: Optional[str]
    first_seen: float
    last_seen: float
    visits: int
    last_request: Optional[str]
    note: Optional[str]

    def memory_line(self) -> str:
        parts = [f"{ordinal(self.visits)} visit"]
        if self.last_request:
            parts.append(f"last asked for {self.last_request}")
        if self.note:
            parts.append(self.note)
        return "Viewer memory: " + ", ".join(parts)


class ViewerMemory:
    def __init__(self, db_path: Optional[os.PathLike | str] = None):
        if db_path is None:
            data_dir = default_data_dir()
            data_dir.mkdir(parents=True, exist_ok=True)
            db_path = data_dir / "halo.db"
        else:
            db_path = Path(db_path)
            db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = Path(db_path)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS viewers (
                    platform TEXT NOT NULL,
                    handle TEXT NOT NULL,
                    profile_id TEXT,
                    first_seen REAL NOT NULL,
                    last_seen REAL NOT NULL,
                    visits INTEGER NOT NULL DEFAULT 1,
                    last_request TEXT,
                    note TEXT,
                    PRIMARY KEY (platform, handle)
                )
                """
            )
            conn.commit()

    def touch(self, platform: str, handle: str) -> tuple[ViewerRecord, bool]:
        """Upsert viewer. Returns (record, is_returning) where is_returning means
        known viewer whose previous last_seen was on an earlier UTC day."""
        platform = (platform or "chat").strip().lower() or "chat"
        handle = (handle or "").strip()
        if not handle:
            raise ValueError("handle required")
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM viewers WHERE platform = ? AND handle = ?",
                (platform, handle),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO viewers
                    (platform, handle, profile_id, first_seen, last_seen, visits, last_request, note)
                    VALUES (?, ?, NULL, ?, ?, 1, NULL, NULL)
                    """,
                    (platform, handle, now, now),
                )
                conn.commit()
                return (
                    ViewerRecord(
                        platform=platform,
                        handle=handle,
                        profile_id=None,
                        first_seen=now,
                        last_seen=now,
                        visits=1,
                        last_request=None,
                        note=None,
                    ),
                    False,
                )

            prev_last = float(row["last_seen"])
            visits = int(row["visits"])
            is_returning = _day_key(prev_last) < _day_key(now)
            if is_returning:
                visits += 1
            conn.execute(
                """
                UPDATE viewers
                SET last_seen = ?, visits = ?
                WHERE platform = ? AND handle = ?
                """,
                (now, visits, platform, handle),
            )
            conn.commit()
            return (
                ViewerRecord(
                    platform=platform,
                    handle=handle,
                    profile_id=row["profile_id"],
                    first_seen=float(row["first_seen"]),
                    last_seen=now,
                    visits=visits,
                    last_request=row["last_request"],
                    note=row["note"],
                ),
                is_returning,
            )

    def get(self, platform: str, handle: str) -> Optional[ViewerRecord]:
        platform = (platform or "chat").strip().lower() or "chat"
        handle = (handle or "").strip()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM viewers WHERE platform = ? AND handle = ?",
                (platform, handle),
            ).fetchone()
        if row is None:
            return None
        return ViewerRecord(
            platform=row["platform"],
            handle=row["handle"],
            profile_id=row["profile_id"],
            first_seen=float(row["first_seen"]),
            last_seen=float(row["last_seen"]),
            visits=int(row["visits"]),
            last_request=row["last_request"],
            note=row["note"],
        )

    def remember_request(
        self,
        platform: str,
        handle: str,
        request: str,
        *,
        note: Optional[str] = None,
    ) -> None:
        platform = (platform or "chat").strip().lower() or "chat"
        handle = (handle or "").strip()
        request = (request or "").strip()[:200]
        if not handle or not request:
            return
        self.touch(platform, handle)
        with self._connect() as conn:
            if note is not None:
                conn.execute(
                    """
                    UPDATE viewers
                    SET last_request = ?, note = ?
                    WHERE platform = ? AND handle = ?
                    """,
                    (request, note, platform, handle),
                )
            else:
                conn.execute(
                    """
                    UPDATE viewers
                    SET last_request = ?
                    WHERE platform = ? AND handle = ?
                    """,
                    (request, platform, handle),
                )
            conn.commit()
