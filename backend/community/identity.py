"""Booth-pass identity: linked platform handles + one-time !link codes."""

from __future__ import annotations

import os
import secrets
import sqlite3
import string
import time
from pathlib import Path
from typing import Any, Optional

from auth import DB_PATH as AUTH_DB_PATH
from auth import _public, user_from_cookie

LINK_TTL_SEC = 10 * 60
CODE_ALPHABET = string.ascii_uppercase + string.digits
CODE_LEN = 6


def _db_path() -> Path:
    raw = os.getenv("DATA_DIR")
    if raw:
        p = Path(raw)
        p.mkdir(parents=True, exist_ok=True)
        return p / "identity.db"
    return Path(AUTH_DB_PATH).resolve().parent / "identity.db"


def _connect(db_path: Optional[os.PathLike | str] = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path is not None else _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Optional[os.PathLike | str] = None) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS linked_handles (
                user_id INTEGER NOT NULL,
                platform TEXT NOT NULL,
                platform_user_id TEXT NOT NULL DEFAULT '',
                handle TEXT NOT NULL,
                linked_at REAL NOT NULL,
                PRIMARY KEY (platform, handle)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_linked_user ON linked_handles(user_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_linked_puid
            ON linked_handles(platform, platform_user_id)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS link_codes (
                code TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expires_at REAL NOT NULL
            )
            """
        )
        conn.commit()


class IdentityStore:
    def __init__(self, db_path: Optional[os.PathLike | str] = None):
        self.db_path = Path(db_path) if db_path is not None else _db_path()
        init_db(self.db_path)

    def _connect(self) -> sqlite3.Connection:
        return _connect(self.db_path)

    def create_link_code(self, user_id: int, *, ttl_sec: float = LINK_TTL_SEC) -> dict[str, Any]:
        user_id = int(user_id)
        now = time.time()
        expires = now + float(ttl_sec)
        with self._connect() as conn:
            conn.execute("DELETE FROM link_codes WHERE user_id = ? OR expires_at < ?", (user_id, now))
            for _ in range(20):
                code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))
                try:
                    conn.execute(
                        "INSERT INTO link_codes(code, user_id, expires_at) VALUES (?,?,?)",
                        (code, user_id, expires),
                    )
                    conn.commit()
                    return {"code": code, "expires_at": expires, "ttl_sec": int(ttl_sec)}
                except sqlite3.IntegrityError:
                    continue
        raise RuntimeError("could not allocate link code")

    def peek_link_code(self, code: str) -> Optional[dict[str, Any]]:
        code = (code or "").strip().upper()
        if not code:
            return None
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT code, user_id, expires_at FROM link_codes WHERE code = ?",
                (code,),
            ).fetchone()
        if not row:
            return None
        return {
            "code": row["code"],
            "user_id": int(row["user_id"]),
            "expires_at": float(row["expires_at"]),
            "expired": float(row["expires_at"]) < now,
        }

    def consume_link_code(
        self,
        code: str,
        *,
        platform: str,
        handle: str,
        platform_user_id: str = "",
    ) -> Optional[dict[str, Any]]:
        """Link platform handle to the user who minted the code. Returns link info or None."""
        code = (code or "").strip().upper()
        platform = (platform or "").strip().lower()
        handle = (handle or "").strip()
        puid = (platform_user_id or handle).strip()
        if not code or not platform or not handle:
            return None
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT user_id, expires_at FROM link_codes WHERE code = ?",
                (code,),
            ).fetchone()
            if not row or float(row["expires_at"]) < now:
                if row:
                    conn.execute("DELETE FROM link_codes WHERE code = ?", (code,))
                    conn.commit()
                return None
            user_id = int(row["user_id"])
            conn.execute("DELETE FROM link_codes WHERE code = ?", (code,))
            conn.execute(
                """
                INSERT INTO linked_handles(user_id, platform, platform_user_id, handle, linked_at)
                VALUES (?,?,?,?,?)
                ON CONFLICT(platform, handle) DO UPDATE SET
                    user_id = excluded.user_id,
                    platform_user_id = excluded.platform_user_id,
                    linked_at = excluded.linked_at
                """,
                (user_id, platform, puid, handle, now),
            )
            conn.commit()
        return {
            "user_id": user_id,
            "platform": platform,
            "handle": handle,
            "platform_user_id": puid,
            "linked_at": now,
        }

    def resolve(
        self,
        platform: str,
        handle: str = "",
        platform_user_id: str = "",
    ) -> Optional[int]:
        """Return booth-pass user_id (profile_id) for a platform identity."""
        platform = (platform or "").strip().lower()
        handle = (handle or "").strip()
        puid = (platform_user_id or "").strip()
        if not platform:
            return None
        with self._connect() as conn:
            if handle:
                row = conn.execute(
                    "SELECT user_id FROM linked_handles WHERE platform = ? AND lower(handle) = lower(?)",
                    (platform, handle),
                ).fetchone()
                if row:
                    return int(row["user_id"])
            if puid:
                row = conn.execute(
                    """
                    SELECT user_id FROM linked_handles
                    WHERE platform = ? AND platform_user_id = ? AND platform_user_id != ''
                    """,
                    (platform, puid),
                ).fetchone()
                if row:
                    return int(row["user_id"])
        return None

    def list_links(self, user_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT user_id, platform, platform_user_id, handle, linked_at
                FROM linked_handles WHERE user_id = ? ORDER BY linked_at DESC
                """,
                (int(user_id),),
            ).fetchall()
        return [
            {
                "user_id": int(r["user_id"]),
                "platform": r["platform"],
                "platform_user_id": r["platform_user_id"],
                "handle": r["handle"],
                "linked_at": float(r["linked_at"]),
            }
            for r in rows
        ]

    def unlink(self, user_id: int, platform: str, handle: str) -> bool:
        platform = (platform or "").strip().lower()
        handle = (handle or "").strip()
        with self._connect() as conn:
            cur = conn.execute(
                """
                DELETE FROM linked_handles
                WHERE user_id = ? AND platform = ? AND lower(handle) = lower(?)
                """,
                (int(user_id), platform, handle),
            )
            conn.commit()
            return cur.rowcount > 0

    def display_name_for(self, profile_id: int) -> Optional[str]:
        """Look up booth-pass display name from auth users DB."""
        try:
            conn = sqlite3.connect(str(AUTH_DB_PATH))
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT display_name FROM users WHERE id = ?",
                (int(profile_id),),
            ).fetchone()
            conn.close()
            if row:
                return str(row["display_name"] or "") or None
        except Exception:
            return None
        return None

    def user_public(self, profile_id: int) -> Optional[dict[str, Any]]:
        try:
            conn = sqlite3.connect(str(AUTH_DB_PATH))
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM users WHERE id = ?", (int(profile_id),)).fetchone()
            conn.close()
            return _public(row) if row else None
        except Exception:
            return None


_default: Optional[IdentityStore] = None


def get_identity() -> IdentityStore:
    global _default
    if _default is None:
        _default = IdentityStore()
    return _default


def set_identity(store: Optional[IdentityStore]) -> None:
    global _default
    _default = store


def require_user(djbb_session: Optional[str]) -> dict[str, Any]:
    from fastapi import HTTPException

    user = user_from_cookie(djbb_session)
    if not user:
        raise HTTPException(401, "Sign in required")
    return user
