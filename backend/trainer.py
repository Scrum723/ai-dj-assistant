"""Idle Halo trainer. Runs only when the room is quiet. Pulls back at Grok v3+."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)
NOTES = Path(__file__).resolve().parent / "halo_training.jsonl"


def training_interval_sec() -> int:
    """Non-use training. Slow down once the buddy is on Grok 3+."""
    model = (os.getenv("GEMINI_MODEL") or os.getenv("XAI_MODEL") or os.getenv("HALO_MODEL") or "").lower()
    level = os.getenv("HALO_TRAINING_LEVEL", "")
    if level.isdigit() and int(level) >= 3:
        return 6 * 60 * 60
    if "grok-3" in model or "grok-4" in model or "gemini-3" in model or "gemini-2.5" in model:
        return 4 * 60 * 60
    return 20 * 60


def _append(note: dict[str, Any]) -> None:
    NOTES.parent.mkdir(parents=True, exist_ok=True)
    with NOTES.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(note, ensure_ascii=False) + "\n")


async def idle_trainer(get_snapshot: Callable[[], dict[str, Any]], stop: asyncio.Event) -> None:
    logger.info("Halo idle trainer armed (interval %ss).", training_interval_sec())
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=training_interval_sec())
            break
        except asyncio.TimeoutError:
            pass
        snap = get_snapshot() or {}
        agent = snap.get("agent") or {}
        if agent.get("dj_mode"):
            continue
        chat = agent.get("chat") or snap.get("chat") or []
        last = chat[0] if chat else None
        _append(
            {
                "ts": int(time.time()),
                "idle": True,
                "viewers": len(agent.get("viewers") or []),
                "mood": agent.get("mood"),
                "last_chat": (last or {}).get("text") or (last or {}).get("message"),
                "model": os.getenv("GEMINI_MODEL") or os.getenv("XAI_MODEL"),
                "interval_sec": training_interval_sec(),
            }
        )
        logger.info("Halo idle note written (%s lines).", sum(1 for _ in NOTES.open()) if NOTES.exists() else 0)
