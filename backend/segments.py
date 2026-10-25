"""Recurring Halo segments: roll call, request hour, guess BPM, track ID, last call."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

BUILTIN_DEFAULTS: list[dict[str, Any]] = [
    {"name": "roll_call", "every_min": 45, "enabled": True, "dj_mode_ok": True},
    {"name": "request_hour", "at_minute": 0, "enabled": True, "dj_mode_ok": False},
    {"name": "guess_the_bpm", "every_min": 30, "enabled": True, "dj_mode_ok": False},
    {"name": "track_id", "enabled": True, "dj_mode_ok": True},  # fired on transition
    {"name": "last_call", "at_minute": 55, "enabled": True, "dj_mode_ok": False},
]

SEGMENT_LINES: dict[str, list[str]] = {
    "roll_call": [
        "Roll call — drop your name and where you're listening from!",
        "Quick roll call: who's in the booth right now? City in the chat.",
    ],
    "request_hour": [
        "Request hour is open — throw a track name in chat and I'll queue what I can.",
        "It's request time. Name a song and I'll dig the crate.",
    ],
    "guess_the_bpm": [
        "Guess the BPM — what's the tempo on this one?",
        "BPM quiz: drop a number in chat for the track spinning now.",
    ],
    "track_id": [
        "Track ID — that was {title}.",
        "Just transitioned into {title}.",
    ],
    "last_call": [
        "Last call for requests this hour — get them in now.",
        "Last call! One more request window before we roll on.",
    ],
}


def merge_segment_config(raw: Any) -> list[dict[str, Any]]:
    """Merge settings halo.segments over built-in defaults by name."""
    by_name = {d["name"]: dict(d) for d in BUILTIN_DEFAULTS}
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            base = by_name.get(name, {"name": name, "enabled": True, "dj_mode_ok": False})
            merged = dict(base)
            merged.update(item)
            merged["name"] = name
            by_name[name] = merged
    return list(by_name.values())


@dataclass
class SegmentEvent:
    name: str
    line: str
    ts: float
    meta: dict[str, Any] = field(default_factory=dict)


class SegmentScheduler:
    """Schedule recurring segments; clock injectable for tests."""

    def __init__(
        self,
        *,
        clock: Optional[Callable[[], float]] = None,
        rng: Optional[Callable[[], float]] = None,
    ):
        self._clock = clock or time.time
        self._rng = rng or (lambda: 0.0)  # deterministic line pick unless overridden
        self._last_every: dict[str, float] = {}
        self._last_at_minute: dict[str, tuple[int, int]] = {}  # name -> (ymd_hour, minute_fired)
        self.current: Optional[SegmentEvent] = None
        self._history: list[SegmentEvent] = []

    def reset(self) -> None:
        self._last_every.clear()
        self._last_at_minute.clear()
        self.current = None
        self._history.clear()

    def _pick_line(self, name: str, **fmt: Any) -> str:
        options = SEGMENT_LINES.get(name) or [f"Segment: {name}"]
        idx = int(self._rng() * len(options)) % len(options)
        line = options[idx]
        try:
            return line.format(**fmt)
        except Exception:
            return line

    def _due_every(self, name: str, every_min: float, now: float) -> bool:
        if every_min is None or float(every_min) <= 0:
            return False
        last = self._last_every.get(name)
        if last is None:
            # First arm: wait a full interval from start so we don't fire on boot
            self._last_every[name] = now
            return False
        return (now - last) >= float(every_min) * 60.0

    def _due_at_minute(self, name: str, at_minute: int, now: float) -> bool:
        try:
            minute = int(at_minute)
        except (TypeError, ValueError):
            return False
        if minute < 0 or minute > 59:
            return False
        dt = datetime.fromtimestamp(now, tz=timezone.utc)
        if dt.minute != minute:
            return False
        key = (dt.year * 1000000 + dt.month * 10000 + dt.day * 100 + dt.hour, minute)
        if self._last_at_minute.get(name) == key:
            return False
        return True

    def mark_fired(self, name: str, now: Optional[float] = None) -> None:
        now = float(self._clock() if now is None else now)
        self._last_every[name] = now
        dt = datetime.fromtimestamp(now, tz=timezone.utc)
        self._last_at_minute[name] = (
            dt.year * 1000000 + dt.month * 10000 + dt.day * 100 + dt.hour,
            dt.minute,
        )

    def _activate(self, name: str, line: str, now: float, **meta: Any) -> SegmentEvent:
        ev = SegmentEvent(name=name, line=line, ts=now, meta=dict(meta))
        self.current = ev
        self._history.append(ev)
        self.mark_fired(name, now)
        return ev

    def tick(
        self,
        configs: list[dict[str, Any]],
        *,
        dj_mode: bool = False,
        now: Optional[float] = None,
        bpm: Optional[float] = None,
        title: Optional[str] = None,
    ) -> Optional[SegmentEvent]:
        """Return at most one due segment event this tick."""
        now = float(self._clock() if now is None else now)
        for cfg in configs:
            name = str(cfg.get("name") or "").strip()
            if not name or name == "track_id":
                continue
            if not cfg.get("enabled", True):
                continue
            if dj_mode and not cfg.get("dj_mode_ok"):
                continue
            due = False
            if cfg.get("every_min") is not None:
                due = self._due_every(name, cfg.get("every_min"), now)
            elif cfg.get("at_minute") is not None:
                due = self._due_at_minute(name, cfg.get("at_minute"), now)
            if not due:
                continue
            fmt: dict[str, Any] = {"title": title or "this track"}
            if name == "guess_the_bpm" and bpm is not None:
                fmt["bpm"] = bpm
            line = self._pick_line(name, **fmt)
            if name == "guess_the_bpm" and bpm is not None:
                # Keep answer in meta for host/operator; don't speak the answer
                return self._activate(name, line, now, bpm=bpm)
            return self._activate(name, line, now)
        return None

    def on_transition(
        self,
        configs: list[dict[str, Any]],
        *,
        title: str,
        dj_mode: bool = False,
        now: Optional[float] = None,
    ) -> Optional[SegmentEvent]:
        """Announce track ID after a mix transition."""
        now = float(self._clock() if now is None else now)
        cfg = next((c for c in configs if c.get("name") == "track_id"), None)
        if cfg is None:
            cfg = {"name": "track_id", "enabled": True, "dj_mode_ok": True}
        if not cfg.get("enabled", True):
            return None
        if dj_mode and not cfg.get("dj_mode_ok", True):
            return None
        line = self._pick_line("track_id", title=title or "the next track")
        return self._activate("track_id", line, now, title=title)

    def status(self) -> dict[str, Any]:
        cur = self.current
        return {
            "current": (
                {"name": cur.name, "line": cur.line, "ts": cur.ts, "meta": cur.meta}
                if cur
                else None
            ),
            "history_len": len(self._history),
        }

    def bpm_answer(self) -> Optional[float]:
        cur = self.current
        if cur and cur.name == "guess_the_bpm":
            bpm = cur.meta.get("bpm")
            try:
                return float(bpm) if bpm is not None else None
            except (TypeError, ValueError):
                return None
        return None
