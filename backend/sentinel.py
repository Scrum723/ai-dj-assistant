"""Defensive Sentinel for DJ Bot Botty.

Logs probes, honeypots, and OUR kill-switch. Does not attack other machines.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

logger = logging.getLogger(__name__)
LOG = Path(__file__).resolve().parent / "sentinel_incidents.jsonl"
router = APIRouter(tags=["sentinel"])
_kill = False


def _client(request: Request) -> dict[str, Any]:
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "")
    ip = ip.split(",")[0].strip()
    return {
        "ip": ip,
        "ua": request.headers.get("user-agent", ""),
        "path": request.url.path,
        "ts": int(time.time()),
    }


def record(kind: str, request: Request, extra: dict[str, Any] | None = None) -> None:
    row = {"kind": kind, **_client(request), **(extra or {})}
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    logger.warning("sentinel %s ip=%s path=%s", kind, row["ip"], row["path"])


def is_killed() -> bool:
    return _kill


@router.get("/admin")
@router.get("/wp-login.php")
@router.get("/.env")
async def honeypot(request: Request):
    record("honeypot", request)
    return PlainTextResponse("not found", status_code=404)


@router.post("/sentinel/kill-ours")
async def kill_ours(request: Request):
    """Operator-only: freeze THIS process's autonomous talk. Never targets a remote host."""
    global _kill
    record("kill_ours", request)
    _kill = True
    return JSONResponse({"ok": True, "scope": "local_process_only"})
