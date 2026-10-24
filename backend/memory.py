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


def _norm_platform(platform: str) -> str:
    return (platform or "chat").strip().lower() or "chat"


def _norm_handle(handle: str) -> str:
    """Lowercase key so visit counts survive casing changes (matches cohost _returning)."""
    return (handle or "").strip().lower()


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

    def _row_to_record(self, row: sqlite3.Row) -> ViewerRecord:
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

    def _merged_for_profile(self, conn: sqlite3.Connection, profile_id: str) -> Optional[ViewerRecord]:
        rows = conn.execute(
            "SELECT * FROM viewers WHERE profile_id = ? ORDER BY last_seen DESC",
            (str(profile_id),),
        ).fetchall()
        if not rows:
            return None
        first_seen = min(float(r["first_seen"]) for r in rows)
        last_seen = max(float(r["last_seen"]) for r in rows)
        visits = max(int(r["visits"]) for r in rows)
        # Prefer most recently updated request/note
        last_request = None
        note = None
        for r in rows:
            if last_request is None and r["last_request"]:
                last_request = r["last_request"]
            if note is None and r["note"]:
                note = r["note"]
        top = rows[0]
        return ViewerRecord(
            platform=top["platform"],
            handle=top["handle"],
            profile_id=str(profile_id),
            first_seen=first_seen,
            last_seen=last_seen,
            visits=visits,
            last_request=last_request,
            note=note,
        )

    def attach_profile(self, platform: str, handle: str, profile_id: str | int) -> Optional[ViewerRecord]:
        """Bind a platform handle to a booth-pass profile and merge visit history."""
        platform = _norm_platform(platform)
        handle = _norm_handle(handle)
        pid = str(profile_id).strip()
        if not handle or not pid:
            return None
        self.touch(platform, handle, profile_id=pid)
        with self._connect() as conn:
            others = conn.execute(
                "SELECT * FROM viewers WHERE profile_id = ?",
                (pid,),
            ).fetchall()
            if len(others) < 2:
                return self._merged_for_profile(conn, pid)
            first_seen = min(float(r["first_seen"]) for r in others)
            visits = max(int(r["visits"]) for r in others)
            # Carry last_request/note from the richest/most recent sibling onto all rows
            best_req = None
            best_note = None
            best_ts = -1.0
            for r in others:
                ts = float(r["last_seen"])
                if r["last_request"] and ts >= best_ts:
                    best_req = r["last_request"]
                    best_ts = ts
                if r["note"]:
                    best_note = r["note"]
            for r in others:
                conn.execute(
                    """
                    UPDATE viewers
                    SET first_seen = ?, visits = ?, last_request = COALESCE(?, last_request),
                        note = COALESCE(?, note)
                    WHERE platform = ? AND handle = ?
                    """,
                    (
                        first_seen,
                        visits,
                        best_req,
                        best_note,
                        r["platform"],
                        r["handle"],
                    ),
                )
            conn.commit()
            return self._merged_for_profile(conn, pid)

    def touch(
        self,
        platform: str,
        handle: str,
        *,
        profile_id: Optional[str | int] = None,
    ) -> tuple[ViewerRecord, bool]:
        """Upsert viewer. Returns (record, is_returning) where is_returning means
        known viewer whose previous last_seen was on an earlier UTC day.
        When profile_id is set, keys/merges across linked handles."""
        platform = _norm_platform(platform)
        handle = _norm_handle(handle)
        if not handle:
            raise ValueError("handle required")
        pid = str(profile_id).strip() if profile_id is not None else None
        if pid == "":
            pid = None
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM viewers WHERE platform = ? AND handle = ?",
                (platform, handle),
            ).fetchone()

            # Also consider prior visits under this profile on other platforms
            profile_prev_last: Optional[float] = None
            profile_visits = 0
            if pid:
                siblings = conn.execute(
                    "SELECT last_seen, visits FROM viewers WHERE profile_id = ?",
                    (pid,),
                ).fetchall()
                for s in siblings:
                    profile_prev_last = max(profile_prev_last or 0.0, float(s["last_seen"]))
                    profile_visits = max(profile_visits, int(s["visits"]))

            if row is None:
                # New handle — seed visits from profile siblings when linking
                visits = max(1, profile_visits) if pid and profile_visits else 1
                is_returning = bool(
                    pid and profile_prev_last is not None and _day_key(profile_prev_last) < _day_key(now)
                )
                if is_returning and profile_visits:
                    visits = profile_visits + 1
                elif is_returning:
                    visits = 2
                first_seen = now
                if pid:
                    merged = self._merged_for_profile(conn, pid)
                    if merged:
                        first_seen = merged.first_seen
                conn.execute(
                    """
                    INSERT INTO viewers
                    (platform, handle, profile_id, first_seen, last_seen, visits, last_request, note)
                    VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)
                    """,
                    (platform, handle, pid, first_seen, now, visits),
                )
                conn.commit()
                rec = ViewerRecord(
                    platform=platform,
                    handle=handle,
                    profile_id=pid,
                    first_seen=first_seen,
                    last_seen=now,
                    visits=visits,
                    last_request=None,
                    note=None,
                )
                if pid:
                    merged = self._merged_for_profile(conn, pid)
                    if merged:
                        return merged, is_returning
                return rec, is_returning

            prev_last = float(row["last_seen"])
            if profile_prev_last is not None:
                prev_last = max(prev_last, profile_prev_last)
            visits = max(int(row["visits"]), profile_visits or 0)
            is_returning = _day_key(prev_last) < _day_key(now)
            if is_returning:
                visits += 1
            new_pid = pid if pid is not None else row["profile_id"]
            first_seen = float(row["first_seen"])
            if new_pid:
                siblings = conn.execute(
                    "SELECT first_seen FROM viewers WHERE profile_id = ?",
                    (str(new_pid),),
                ).fetchall()
                if siblings:
                    first_seen = min(first_seen, *(float(s["first_seen"]) for s in siblings))
            conn.execute(
                """
                UPDATE viewers
                SET last_seen = ?, visits = ?, profile_id = ?, first_seen = ?
                WHERE platform = ? AND handle = ?
                """,
                (now, visits, new_pid, first_seen, platform, handle),
            )
            conn.commit()
            rec = ViewerRecord(
                platform=platform,
                handle=handle,
                profile_id=new_pid,
                first_seen=first_seen,
                last_seen=now,
                visits=visits,
                last_request=row["last_request"],
                note=row["note"],
            )
            if new_pid:
                merged = self._merged_for_profile(conn, str(new_pid))
                if merged:
                    return merged, is_returning
            return rec, is_returning

    def get(
        self,
        platform: str,
        handle: str,
        *,
        profile_id: Optional[str | int] = None,
    ) -> Optional[ViewerRecord]:
        platform = _norm_platform(platform)
        handle = _norm_handle(handle)
        pid = str(profile_id).strip() if profile_id is not None else None
        with self._connect() as conn:
            if pid:
                merged = self._merged_for_profile(conn, pid)
                if merged:
                    return merged
            if not handle:
                return None
            row = conn.execute(
                "SELECT * FROM viewers WHERE platform = ? AND handle = ?",
                (platform, handle),
            ).fetchone()
            if row is None:
                return None
            if row["profile_id"]:
                merged = self._merged_for_profile(conn, str(row["profile_id"]))
                if merged:
                    return merged
            return self._row_to_record(row)

    def get_by_profile(self, profile_id: str | int) -> Optional[ViewerRecord]:
        pid = str(profile_id).strip()
        if not pid:
            return None
        with self._connect() as conn:
            return self._merged_for_profile(conn, pid)

    def remember_request(
        self,
        platform: str,
        handle: str,
        request: str,
        *,
        note: Optional[str] = None,
        profile_id: Optional[str | int] = None,
    ) -> None:
        platform = _norm_platform(platform)
        handle = _norm_handle(handle)
        request = (request or "").strip()[:200]
        if not handle or not request:
            return
        self.touch(platform, handle, profile_id=profile_id)
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
            # Mirror onto other profile rows so merge stays consistent
            row = conn.execute(
                "SELECT profile_id FROM viewers WHERE platform = ? AND handle = ?",
                (platform, handle),
            ).fetchone()
            pid = (profile_id and str(profile_id)) or (row["profile_id"] if row else None)
            if pid:
                if note is not None:
                    conn.execute(
                        "UPDATE viewers SET last_request = ?, note = ? WHERE profile_id = ?",
                        (request, note, str(pid)),
                    )
                else:
                    conn.execute(
                        "UPDATE viewers SET last_request = ? WHERE profile_id = ?",
                        (request, str(pid)),
                    )
            conn.commit()
