"""Unified room moderation: bans/timeouts by profile or platform+handle."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def default_data_dir() -> Path:
    raw = os.getenv("DATA_DIR")
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[1]


class CommunityMod:
    """Room-level bans + audit log. Expiry is checked on read (until <= now)."""

    def __init__(
        self,
        db_path: Optional[os.PathLike | str] = None,
        *,
        clock=None,
        identity: Any = None,
        get_settings: Optional[Any] = None,
        get_adapters: Optional[Any] = None,
        broadcast: Optional[Any] = None,
    ):
        if db_path is None:
            data_dir = default_data_dir()
            data_dir.mkdir(parents=True, exist_ok=True)
            db_path = data_dir / "community.db"
        else:
            db_path = Path(db_path)
            db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = Path(db_path)
        self._clock = clock or time.time
        self._identity = identity
        self._get_settings = get_settings or (lambda: {})
        self._get_adapters = get_adapters or (lambda: {})
        self._broadcast = broadcast
        self._lock = threading.Lock()
        self._init_db()

    def set_identity(self, identity: Any) -> None:
        self._identity = identity

    def set_broadcast(self, broadcast: Any) -> None:
        self._broadcast = broadcast

    def set_get_adapters(self, get_adapters: Any) -> None:
        self._get_adapters = get_adapters

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
                CREATE TABLE IF NOT EXISTS bans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_profile_id INTEGER,
                    platform TEXT NOT NULL DEFAULT '',
                    handle TEXT NOT NULL DEFAULT '',
                    until_ts REAL,
                    reason TEXT NOT NULL DEFAULT '',
                    by_user_id INTEGER,
                    by_name TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_bans_active ON bans(active, until_ts)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_bans_profile ON bans(target_profile_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_bans_handle ON bans(platform, handle)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mod_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT NOT NULL,
                    target_profile_id INTEGER,
                    platform TEXT NOT NULL DEFAULT '',
                    handle TEXT NOT NULL DEFAULT '',
                    message_id TEXT,
                    reason TEXT NOT NULL DEFAULT '',
                    by_user_id INTEGER,
                    by_name TEXT NOT NULL DEFAULT '',
                    until_ts REAL,
                    ts REAL NOT NULL,
                    meta_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_mod_log_ts ON mod_log(ts DESC)"
            )
            conn.commit()

    def _expire_stale(self, conn: sqlite3.Connection) -> None:
        now = self._now()
        conn.execute(
            """
            UPDATE bans SET active = 0
            WHERE active = 1 AND until_ts IS NOT NULL AND until_ts <= ?
            """,
            (now,),
        )

    def is_banned(
        self,
        *,
        profile_id: Optional[int] = None,
        platform: str = "",
        handle: str = "",
    ) -> bool:
        platform = (platform or "").strip().lower()
        handle = (handle or "").strip()
        with self._lock:
            with self._connect() as conn:
                self._expire_stale(conn)
                conn.commit()
                if profile_id is not None:
                    try:
                        pid = int(profile_id)
                    except (TypeError, ValueError):
                        pid = None
                    if pid is not None:
                        row = conn.execute(
                            """
                            SELECT 1 FROM bans
                            WHERE active = 1 AND target_profile_id = ?
                            LIMIT 1
                            """,
                            (pid,),
                        ).fetchone()
                        if row:
                            return True
                if platform and handle:
                    row = conn.execute(
                        """
                        SELECT 1 FROM bans
                        WHERE active = 1 AND platform = ? AND lower(handle) = lower(?)
                        LIMIT 1
                        """,
                        (platform, handle),
                    ).fetchone()
                    if row:
                        return True
        return False

    def _log(
        self,
        conn: sqlite3.Connection,
        *,
        action: str,
        target_profile_id: Optional[int] = None,
        platform: str = "",
        handle: str = "",
        message_id: Optional[str] = None,
        reason: str = "",
        by_user_id: Optional[int] = None,
        by_name: str = "",
        until_ts: Optional[float] = None,
        meta: Optional[dict] = None,
    ) -> int:
        cur = conn.execute(
            """
            INSERT INTO mod_log
            (action, target_profile_id, platform, handle, message_id, reason,
             by_user_id, by_name, until_ts, ts, meta_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                action,
                target_profile_id,
                platform or "",
                handle or "",
                message_id,
                reason or "",
                by_user_id,
                by_name or "",
                until_ts,
                self._now(),
                json.dumps(meta or {}),
            ),
        )
        return int(cur.lastrowid)

    def ban(
        self,
        *,
        target_profile_id: Optional[int] = None,
        platform: str = "",
        handle: str = "",
        until: Optional[float] = None,
        reason: str = "",
        by_user_id: Optional[int] = None,
        by_name: str = "",
        push: Optional[bool] = None,
    ) -> dict[str, Any]:
        """Permanent ban when until is None. Optional push to platform adapters."""
        platform = (platform or "").strip().lower()
        handle = (handle or "").strip()
        if target_profile_id is None and not (platform and handle):
            raise ValueError("ban requires target_profile_id or platform+handle")
        pid = None
        if target_profile_id is not None:
            pid = int(target_profile_id)
        with self._lock:
            with self._connect() as conn:
                self._expire_stale(conn)
                # Deactivate overlapping active bans for same target
                if pid is not None:
                    conn.execute(
                        "UPDATE bans SET active = 0 WHERE active = 1 AND target_profile_id = ?",
                        (pid,),
                    )
                if platform and handle:
                    conn.execute(
                        """
                        UPDATE bans SET active = 0
                        WHERE active = 1 AND platform = ? AND lower(handle) = lower(?)
                        """,
                        (platform, handle),
                    )
                cur = conn.execute(
                    """
                    INSERT INTO bans
                    (target_profile_id, platform, handle, until_ts, reason,
                     by_user_id, by_name, created_at, active)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        pid,
                        platform,
                        handle,
                        until,
                        reason or "",
                        by_user_id,
                        by_name or "",
                        self._now(),
                    ),
                )
                ban_id = int(cur.lastrowid)
                self._log(
                    conn,
                    action="ban" if until is None else "timeout",
                    target_profile_id=pid,
                    platform=platform,
                    handle=handle,
                    reason=reason,
                    by_user_id=by_user_id,
                    by_name=by_name,
                    until_ts=until,
                    meta={"ban_id": ban_id},
                )
                conn.commit()
        push_result = self._maybe_push(
            profile_id=pid,
            platform=platform,
            handle=handle,
            until=until,
            push=push,
        )
        return {
            "ok": True,
            "ban_id": ban_id,
            "target_profile_id": pid,
            "platform": platform,
            "handle": handle,
            "until": until,
            "reason": reason or "",
            "push": push_result,
        }

    def timeout(
        self,
        *,
        seconds: int,
        target_profile_id: Optional[int] = None,
        platform: str = "",
        handle: str = "",
        reason: str = "",
        by_user_id: Optional[int] = None,
        by_name: str = "",
        push: Optional[bool] = None,
    ) -> dict[str, Any]:
        secs = max(1, int(seconds))
        until = self._now() + secs
        return self.ban(
            target_profile_id=target_profile_id,
            platform=platform,
            handle=handle,
            until=until,
            reason=reason,
            by_user_id=by_user_id,
            by_name=by_name,
            push=push,
        )

    def unban(
        self,
        *,
        target_profile_id: Optional[int] = None,
        platform: str = "",
        handle: str = "",
        reason: str = "",
        by_user_id: Optional[int] = None,
        by_name: str = "",
    ) -> dict[str, Any]:
        platform = (platform or "").strip().lower()
        handle = (handle or "").strip()
        if target_profile_id is None and not (platform and handle):
            raise ValueError("unban requires target_profile_id or platform+handle")
        cleared = 0
        with self._lock:
            with self._connect() as conn:
                if target_profile_id is not None:
                    cur = conn.execute(
                        "UPDATE bans SET active = 0 WHERE active = 1 AND target_profile_id = ?",
                        (int(target_profile_id),),
                    )
                    cleared += int(cur.rowcount or 0)
                if platform and handle:
                    cur = conn.execute(
                        """
                        UPDATE bans SET active = 0
                        WHERE active = 1 AND platform = ? AND lower(handle) = lower(?)
                        """,
                        (platform, handle),
                    )
                    cleared += int(cur.rowcount or 0)
                self._log(
                    conn,
                    action="unban",
                    target_profile_id=int(target_profile_id) if target_profile_id is not None else None,
                    platform=platform,
                    handle=handle,
                    reason=reason,
                    by_user_id=by_user_id,
                    by_name=by_name,
                    meta={"cleared": cleared},
                )
                conn.commit()
        return {"ok": True, "cleared": cleared}

    def delete_message(
        self,
        message_id: str,
        *,
        reason: str = "",
        by_user_id: Optional[int] = None,
        by_name: str = "",
        platform: str = "",
        handle: str = "",
        target_profile_id: Optional[int] = None,
    ) -> dict[str, Any]:
        mid = (message_id or "").strip()
        if not mid:
            raise ValueError("message_id required")
        with self._lock:
            with self._connect() as conn:
                log_id = self._log(
                    conn,
                    action="delete",
                    target_profile_id=target_profile_id,
                    platform=platform or "",
                    handle=handle or "",
                    message_id=mid,
                    reason=reason,
                    by_user_id=by_user_id,
                    by_name=by_name,
                )
                conn.commit()
        payload = {"id": mid, "reason": reason or "", "by": by_name or ""}
        if self._broadcast:
            try:
                self._broadcast({"type": "delete-message", "payload": payload})
            except Exception:
                logger.exception("delete-message broadcast failed")
        return {"ok": True, "message_id": mid, "log_id": log_id}

    def list_log(self, *, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 100), 500))
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT id, action, target_profile_id, platform, handle, message_id,
                           reason, by_user_id, by_name, until_ts, ts, meta_json
                    FROM mod_log ORDER BY ts DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        out = []
        for r in rows:
            try:
                meta = json.loads(r["meta_json"] or "{}")
            except Exception:
                meta = {}
            out.append(
                {
                    "id": int(r["id"]),
                    "action": r["action"],
                    "target_profile_id": r["target_profile_id"],
                    "platform": r["platform"] or "",
                    "handle": r["handle"] or "",
                    "message_id": r["message_id"],
                    "reason": r["reason"] or "",
                    "by_user_id": r["by_user_id"],
                    "by_name": r["by_name"] or "",
                    "until": r["until_ts"],
                    "ts": float(r["ts"]),
                    "meta": meta,
                }
            )
        return out

    def active_bans(self) -> list[dict[str, Any]]:
        with self._lock:
            with self._connect() as conn:
                self._expire_stale(conn)
                conn.commit()
                rows = conn.execute(
                    """
                    SELECT id, target_profile_id, platform, handle, until_ts, reason,
                           by_user_id, by_name, created_at
                    FROM bans WHERE active = 1 ORDER BY created_at DESC
                    """
                ).fetchall()
        return [
            {
                "id": int(r["id"]),
                "target_profile_id": r["target_profile_id"],
                "platform": r["platform"] or "",
                "handle": r["handle"] or "",
                "until": r["until_ts"],
                "reason": r["reason"] or "",
                "by_user_id": r["by_user_id"],
                "by_name": r["by_name"] or "",
                "created_at": float(r["created_at"]),
            }
            for r in rows
        ]

    def _push_enabled(self, push: Optional[bool]) -> bool:
        if push is not None:
            return bool(push)
        cfg = self._get_settings() or {}
        return bool((cfg.get("community") or {}).get("push_bans"))

    def _handles_to_push(
        self,
        *,
        profile_id: Optional[int],
        platform: str,
        handle: str,
    ) -> list[tuple[str, str]]:
        """(platform, handle) pairs for adapter ban push."""
        pairs: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        if platform and handle:
            key = (platform, handle.lower())
            if key not in seen:
                seen.add(key)
                pairs.append((platform, handle))
        if profile_id is not None and self._identity is not None:
            try:
                links = self._identity.list_links(int(profile_id))
            except Exception:
                logger.exception("list_links for push failed")
                links = []
            for link in links or []:
                plat = str(link.get("platform") or "").strip().lower()
                h = str(link.get("handle") or "").strip()
                if not plat or not h:
                    continue
                key = (plat, h.lower())
                if key in seen:
                    continue
                seen.add(key)
                pairs.append((plat, h))
        return pairs

    def _maybe_push(
        self,
        *,
        profile_id: Optional[int],
        platform: str,
        handle: str,
        until: Optional[float],
        push: Optional[bool],
    ) -> dict[str, Any]:
        if not self._push_enabled(push):
            return {"enabled": False, "results": []}
        seconds: Optional[int]
        if until is None:
            seconds = None  # permanent — adapters may treat as long ban
        else:
            seconds = max(1, int(until - self._now()))
        adapters = self._get_adapters() or {}
        results = []
        for plat, h in self._handles_to_push(
            profile_id=profile_id, platform=platform, handle=handle
        ):
            adapter = adapters.get(plat)
            if adapter is None:
                results.append({"platform": plat, "handle": h, "ok": False, "reason": "no_adapter"})
                continue
            if not getattr(adapter, "can_moderate", False):
                results.append({"platform": plat, "handle": h, "ok": False, "reason": "cannot_moderate"})
                continue
            ban_fn = getattr(adapter, "ban", None)
            if not callable(ban_fn):
                results.append({"platform": plat, "handle": h, "ok": False, "reason": "no_ban"})
                continue
            try:
                ban_fn(h, seconds)
                results.append({"platform": plat, "handle": h, "ok": True, "seconds": seconds})
            except Exception as exc:
                logger.exception("push ban failed platform=%s handle=%s", plat, h)
                results.append(
                    {"platform": plat, "handle": h, "ok": False, "reason": str(exc)[:200]}
                )
        return {"enabled": True, "results": results}


_mod: Optional[CommunityMod] = None


def get_mod() -> Optional[CommunityMod]:
    return _mod


def set_mod(mod: Optional[CommunityMod]) -> None:
    global _mod
    _mod = mod
