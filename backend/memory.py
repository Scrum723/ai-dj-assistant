"""Per-viewer memory for Halo — SQLite keyed by platform+handle."""

from __future__ import annotations

import os
import re
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


def _utc_day_ordinal(ts: float) -> int:
    return datetime.fromtimestamp(ts, tz=timezone.utc).toordinal()


def next_streak(prev_last: Optional[float], prev_streak: int, now: float) -> int:
    """Consecutive UTC-day streak. Same day keeps; yesterday +1; else reset to 1."""
    if prev_last is None:
        return 1
    today = _utc_day_ordinal(now)
    prev = _utc_day_ordinal(prev_last)
    if prev == today:
        return max(1, int(prev_streak or 0)) or 1
    if prev == today - 1:
        return max(1, int(prev_streak or 0)) + 1
    return 1


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


MILESTONE_VISITS = (5, 10, 25)
# Retention badges (B8) — distinct from greeting milestones
BADGE_THRESHOLDS = (3, 10, 25)
BADGE_LABELS = {3: "regular", 10: "loyal", 25: "legend"}


def retention_badges(visits: int) -> list[str]:
    """Badge labels earned at 3 / 10 / 25 visits (linked platforms count)."""
    visits = int(visits or 0)
    return [BADGE_LABELS[t] for t in BADGE_THRESHOLDS if visits >= t]


def retention_summary(visits: int, streak: int = 0) -> dict:
    return {
        "visits": int(visits or 0),
        "streak": int(streak or 0),
        "badges": retention_badges(visits),
    }

# Common genre tokens pulled from request text
_GENRE_TOKENS = (
    "dnb",
    "drum and bass",
    "drum & bass",
    "house",
    "techno",
    "trance",
    "dubstep",
    "garage",
    "breakbeat",
    "breaks",
    "ambient",
    "jungle",
    "hardstyle",
    "trap",
    "hip hop",
    "hip-hop",
    "rap",
    "edm",
    "synthwave",
    "disco",
    "funk",
    "soul",
    "bass",
    "ukg",
    "footwork",
)

_WHERE_FROM_RE = re.compile(
    r"(?i)\b(?:(?:i(?:'?m| am)\s+(?:from|in)|from|listening\s+from|watching\s+from|"
    r"tuning\s+in\s+from|here\s+from|calling\s+in\s+from)\s+)"
    r"([A-Za-z][A-Za-z .'\-]{1,40})"
)
_WHERE_TRAIL_RE = re.compile(
    r"(?i)\s+(and|but|so|lol|haha|tonight|today|chat|here)\b.*$"
)


def parse_where_from(message: str) -> Optional[str]:
    """Extract an opt-in location phrase if the viewer volunteered it."""
    text = (message or "").strip()
    if not text:
        return None
    m = _WHERE_FROM_RE.search(text)
    if not m:
        return None
    place = m.group(1).strip(" .,!?;:")
    place = _WHERE_TRAIL_RE.sub("", place).strip(" .,!?;:")
    if len(place) < 2 or len(place) > 48:
        return None
    return place


def genres_from_request(query: str) -> list[str]:
    q = (query or "").strip().lower()
    if not q:
        return []
    found: list[str] = []
    for token in _GENRE_TOKENS:
        if token in q and token not in found:
            found.append(token)
    return found


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
    where_from: Optional[str] = None
    favourite_genres: Optional[str] = None  # comma-separated
    streak: int = 0

    def genres_list(self) -> list[str]:
        raw = (self.favourite_genres or "").strip()
        if not raw:
            return []
        return [g.strip() for g in raw.split(",") if g.strip()]

    def badges(self) -> list[str]:
        return retention_badges(self.visits)

    def retention(self) -> dict:
        return retention_summary(self.visits, self.streak)

    def memory_line(self) -> str:
        parts = [f"{ordinal(self.visits)} visit"]
        if self.streak and self.streak > 1:
            parts.append(f"{self.streak}-day streak")
        if self.last_request:
            parts.append(f"last asked for {self.last_request}")
        if self.where_from:
            parts.append(f"listening from {self.where_from}")
        genres = self.genres_list()
        if genres:
            parts.append("likes " + ", ".join(genres[:4]))
        if self.note:
            parts.append(self.note)
        return "Viewer memory: " + ", ".join(parts)


class GreetingShowState:
    """Greeting stages that fire at most once per profile per show."""

    STAGES = ("first_time", "returning", "milestone")

    def __init__(self) -> None:
        self._fired: dict[str, set[str]] = {}

    def reset_show(self) -> None:
        self._fired.clear()

    @staticmethod
    def key(platform: str, handle: str, profile_id: Optional[str | int] = None) -> str:
        if profile_id is not None and str(profile_id).strip():
            return f"profile:{str(profile_id).strip()}"
        return f"{_norm_platform(platform)}:{_norm_handle(handle)}"

    def already(self, key: str, stage: str) -> bool:
        return stage in self._fired.get(key, set())

    def mark(self, key: str, stage: str) -> None:
        self._fired.setdefault(key, set()).add(stage)

    def next_stage(
        self,
        *,
        visits: int,
        is_new_day_return: bool,
        key: str,
    ) -> Optional[str]:
        """Pick greeting stage for this appearance. None if already greeted this show."""
        visits = int(visits or 0)
        fired = self._fired.setdefault(key, set())
        if visits <= 1:
            return None if "first_time" in fired else "first_time"
        if visits in MILESTONE_VISITS and "milestone" not in fired:
            return "milestone"
        if (is_new_day_return or visits >= 2) and "returning" not in fired:
            return "returning"
        return None


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
                    where_from TEXT,
                    favourite_genres TEXT,
                    PRIMARY KEY (platform, handle)
                )
                """
            )
            cols = {r[1] for r in conn.execute("PRAGMA table_info(viewers)").fetchall()}
            if "where_from" not in cols:
                conn.execute("ALTER TABLE viewers ADD COLUMN where_from TEXT")
            if "favourite_genres" not in cols:
                conn.execute("ALTER TABLE viewers ADD COLUMN favourite_genres TEXT")
            if "streak" not in cols:
                conn.execute("ALTER TABLE viewers ADD COLUMN streak INTEGER NOT NULL DEFAULT 0")
            conn.commit()

    def _row_to_record(self, row: sqlite3.Row) -> ViewerRecord:
        keys = row.keys()
        return ViewerRecord(
            platform=row["platform"],
            handle=row["handle"],
            profile_id=row["profile_id"],
            first_seen=float(row["first_seen"]),
            last_seen=float(row["last_seen"]),
            visits=int(row["visits"]),
            last_request=row["last_request"],
            note=row["note"],
            where_from=row["where_from"] if "where_from" in keys else None,
            favourite_genres=row["favourite_genres"] if "favourite_genres" in keys else None,
            streak=int(row["streak"]) if "streak" in keys and row["streak"] is not None else 0,
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
        streak = 0
        last_request = None
        note = None
        where_from = None
        genre_set: list[str] = []
        for r in rows:
            keys = r.keys()
            if "streak" in keys and r["streak"] is not None:
                streak = max(streak, int(r["streak"]))
            if last_request is None and r["last_request"]:
                last_request = r["last_request"]
            if note is None and r["note"]:
                note = r["note"]
            if where_from is None and "where_from" in keys and r["where_from"]:
                where_from = r["where_from"]
            if "favourite_genres" in keys and r["favourite_genres"]:
                for g in str(r["favourite_genres"]).split(","):
                    g = g.strip()
                    if g and g not in genre_set:
                        genre_set.append(g)
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
            where_from=where_from,
            favourite_genres=",".join(genre_set) if genre_set else None,
            streak=streak,
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
            streak = 0
            for r in others:
                keys = r.keys()
                if "streak" in keys and r["streak"] is not None:
                    streak = max(streak, int(r["streak"]))
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
                    SET first_seen = ?, visits = ?, streak = ?, last_request = COALESCE(?, last_request),
                        note = COALESCE(?, note)
                    WHERE platform = ? AND handle = ?
                    """,
                    (
                        first_seen,
                        visits,
                        streak,
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
            profile_streak = 0
            if pid:
                siblings = conn.execute(
                    "SELECT last_seen, visits, streak FROM viewers WHERE profile_id = ?",
                    (pid,),
                ).fetchall()
                for s in siblings:
                    profile_prev_last = max(profile_prev_last or 0.0, float(s["last_seen"]))
                    profile_visits = max(profile_visits, int(s["visits"]))
                    sk = s["streak"] if "streak" in s.keys() else 0
                    if sk is not None:
                        profile_streak = max(profile_streak, int(sk))

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
                streak = next_streak(
                    profile_prev_last if pid else None,
                    profile_streak,
                    now,
                )
                first_seen = now
                if pid:
                    merged = self._merged_for_profile(conn, pid)
                    if merged:
                        first_seen = merged.first_seen
                conn.execute(
                    """
                    INSERT INTO viewers
                    (platform, handle, profile_id, first_seen, last_seen, visits,
                     last_request, note, where_from, favourite_genres, streak)
                    VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?)
                    """,
                    (platform, handle, pid, first_seen, now, visits, streak),
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
                    where_from=None,
                    favourite_genres=None,
                    streak=streak,
                )
                if pid:
                    merged = self._merged_for_profile(conn, pid)
                    if merged:
                        return merged, is_returning
                return rec, is_returning

            prev_last = float(row["last_seen"])
            if profile_prev_last is not None:
                prev_last = max(prev_last, profile_prev_last)
            keys = row.keys()
            row_streak = int(row["streak"]) if "streak" in keys and row["streak"] is not None else 0
            prev_streak = max(row_streak, profile_streak)
            visits = max(int(row["visits"]), profile_visits or 0)
            is_returning = _day_key(prev_last) < _day_key(now)
            if is_returning:
                visits += 1
            streak = next_streak(prev_last, prev_streak, now)
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
                SET last_seen = ?, visits = ?, profile_id = ?, first_seen = ?, streak = ?
                WHERE platform = ? AND handle = ?
                """,
                (now, visits, new_pid, first_seen, streak, platform, handle),
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
                where_from=row["where_from"] if "where_from" in keys else None,
                favourite_genres=row["favourite_genres"] if "favourite_genres" in keys else None,
                streak=streak,
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

    def retention_for(
        self,
        *,
        platform: str = "",
        handle: str = "",
        profile_id: Optional[str | int] = None,
    ) -> dict:
        rec = None
        if profile_id is not None and str(profile_id).strip():
            rec = self.get_by_profile(profile_id)
        if rec is None and handle:
            rec = self.get(platform or "chat", handle, profile_id=profile_id)
        if rec is None:
            return retention_summary(0, 0)
        return rec.retention()

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
        genres = genres_from_request(request)
        if genres:
            self.add_genres(platform, handle, genres, profile_id=profile_id)
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

    def set_where_from(
        self,
        platform: str,
        handle: str,
        where_from: str,
        *,
        profile_id: Optional[str | int] = None,
    ) -> None:
        platform = _norm_platform(platform)
        handle = _norm_handle(handle)
        place = (where_from or "").strip()[:80]
        if not handle or not place:
            return
        self.touch(platform, handle, profile_id=profile_id)
        with self._connect() as conn:
            conn.execute(
                "UPDATE viewers SET where_from = ? WHERE platform = ? AND handle = ?",
                (place, platform, handle),
            )
            row = conn.execute(
                "SELECT profile_id FROM viewers WHERE platform = ? AND handle = ?",
                (platform, handle),
            ).fetchone()
            pid = (profile_id and str(profile_id)) or (row["profile_id"] if row else None)
            if pid:
                conn.execute(
                    "UPDATE viewers SET where_from = ? WHERE profile_id = ?",
                    (place, str(pid)),
                )
            conn.commit()

    def add_genres(
        self,
        platform: str,
        handle: str,
        genres: list[str],
        *,
        profile_id: Optional[str | int] = None,
    ) -> None:
        platform = _norm_platform(platform)
        handle = _norm_handle(handle)
        incoming = [g.strip().lower() for g in (genres or []) if g and g.strip()]
        if not handle or not incoming:
            return
        self.touch(platform, handle, profile_id=profile_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT favourite_genres, profile_id FROM viewers WHERE platform = ? AND handle = ?",
                (platform, handle),
            ).fetchone()
            existing: list[str] = []
            if row and row["favourite_genres"]:
                existing = [g.strip() for g in str(row["favourite_genres"]).split(",") if g.strip()]
            for g in incoming:
                if g not in existing:
                    existing.append(g)
            existing = existing[:8]
            joined = ",".join(existing)
            conn.execute(
                "UPDATE viewers SET favourite_genres = ? WHERE platform = ? AND handle = ?",
                (joined, platform, handle),
            )
            pid = (profile_id and str(profile_id)) or (row["profile_id"] if row else None)
            if pid:
                conn.execute(
                    "UPDATE viewers SET favourite_genres = ? WHERE profile_id = ?",
                    (joined, str(pid)),
                )
            conn.commit()

    def list_listening_from(self, *, limit: int = 40) -> list[dict]:
        """Opt-in where_from entries for Room UI (most recent first)."""
        limit = max(1, min(int(limit), 100))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT handle, platform, where_from, profile_id, last_seen
                FROM viewers
                WHERE where_from IS NOT NULL AND TRIM(where_from) != ''
                ORDER BY last_seen DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        seen: set[str] = set()
        out: list[dict] = []
        for r in rows:
            place = (r["where_from"] or "").strip()
            if not place:
                continue
            dedupe = str(r["profile_id"] or "") or f"{r['platform']}:{r['handle']}"
            if dedupe in seen:
                continue
            seen.add(dedupe)
            out.append(
                {
                    "handle": r["handle"],
                    "platform": r["platform"],
                    "where_from": place,
                    "profile_id": r["profile_id"],
                }
            )
        return out
