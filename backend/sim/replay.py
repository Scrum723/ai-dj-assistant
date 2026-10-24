"""Parse recorded-set replays: JSON list or rekordbox history-style export."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Optional, Union

PathLike = Union[str, Path]


def _as_float(v: Any, default: float = 0.0) -> float:
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _as_track_id(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, int):
        return v
    s = str(v).strip()
    if s.isdigit():
        return int(s)
    return s


def normalize_entry(raw: dict) -> dict:
    """Normalize one cue entry to {track_id, start_sec, ...optional meta}."""
    tid = (
        raw.get("track_id")
        or raw.get("TrackID")
        or raw.get("Id")
        or raw.get("id")
        or raw.get("ContentID")
    )
    start = raw.get("start_sec")
    if start is None:
        start = raw.get("StartTime") or raw.get("start") or raw.get("time") or raw.get("Position")
    # rekordbox sometimes uses mm:ss.xxx
    if isinstance(start, str) and ":" in start:
        start = _parse_timestamp(start)
    entry = {
        "track_id": _as_track_id(tid),
        "start_sec": _as_float(start, 0.0),
    }
    for k in ("filename", "title", "artist", "bpm", "camelot", "key", "energy", "outro_sec", "duration_sec"):
        if raw.get(k) is not None:
            entry[k] = raw[k]
    return entry


_TS_RE = re.compile(
    r"^(?:(?P<h>\d+):)?(?P<m>\d+):(?P<s>\d+(?:\.\d+)?)$"
)


def _parse_timestamp(s: str) -> float:
    m = _TS_RE.match(s.strip())
    if not m:
        return _as_float(s, 0.0)
    h = int(m.group("h") or 0)
    mi = int(m.group("m") or 0)
    sec = float(m.group("s") or 0)
    return h * 3600 + mi * 60 + sec


def parse_json_replay(data: Any) -> list[dict]:
    if isinstance(data, str):
        data = json.loads(data)
    if isinstance(data, dict):
        data = data.get("tracks") or data.get("history") or data.get("entries") or []
    if not isinstance(data, list):
        raise ValueError("replay JSON must be a list or {tracks|history|entries: [...]}")
    return [normalize_entry(x if isinstance(x, dict) else {"track_id": x, "start_sec": 0.0}) for x in data]


def parse_rekordbox_history_csv(text: str) -> list[dict]:
    """Best-effort parse of rekordbox history / playlist CSV export."""
    lines = text.splitlines()
    # Skip metadata rows until a header with Track or Title
    header_idx = 0
    for i, line in enumerate(lines):
        low = line.lower()
        if "track" in low or "title" in low or "contentid" in low or "start" in low:
            header_idx = i
            break
    reader = csv.DictReader(lines[header_idx:])
    entries: list[dict] = []
    t = 0.0
    for row in reader:
        # normalize keys
        row_l = {((k or "").strip()): (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
        # try common column names
        tid = (
            row_l.get("TrackID")
            or row_l.get("ContentID")
            or row_l.get("Id")
            or row_l.get("track_id")
            or row_l.get("#")
        )
        title = row_l.get("Track Title") or row_l.get("Title") or row_l.get("filename")
        artist = row_l.get("Artist") or row_l.get("artist")
        start_raw = (
            row_l.get("Start Time")
            or row_l.get("StartTime")
            or row_l.get("start_sec")
            or row_l.get("Time")
        )
        if start_raw:
            start = _parse_timestamp(str(start_raw)) if isinstance(start_raw, str) else _as_float(start_raw)
        else:
            start = t
        dur = _as_float(row_l.get("Duration") or row_l.get("Time") or row_l.get("duration_sec"), 0.0)
        if dur > 0 and not start_raw:
            # Duration column may be mm:ss — if we used Time as duration, re-parse
            pass
        entry = normalize_entry(
            {
                "track_id": tid or title or f"row-{len(entries)+1}",
                "start_sec": start,
                "filename": title,
                "artist": artist,
                "bpm": row_l.get("BPM") or row_l.get("bpm"),
                "key": row_l.get("Key") or row_l.get("key"),
            }
        )
        entries.append(entry)
        # Advance cursor if sequential without absolute starts
        if not start_raw:
            # try duration mm:ss in Duration
            d_raw = row_l.get("Duration") or ""
            if d_raw and ":" in str(d_raw):
                t = start + _parse_timestamp(str(d_raw))
            elif dur > 60:  # likely already seconds
                t = start + dur
            else:
                t = start + 180.0
        else:
            t = start
    return entries


def load_replay(path: PathLike) -> list[dict]:
    p = Path(path)
    text = p.read_text(encoding="utf-8-sig")
    suffix = p.suffix.lower()
    if suffix == ".json":
        return parse_json_replay(text)
    if suffix in (".csv", ".txt"):
        # Heuristic: JSON-looking?
        stripped = text.lstrip()
        if stripped.startswith("[") or stripped.startswith("{"):
            return parse_json_replay(text)
        return parse_rekordbox_history_csv(text)
    # Try JSON then CSV
    try:
        return parse_json_replay(text)
    except Exception:
        return parse_rekordbox_history_csv(text)


def entries_to_tracks(entries: list[dict], library: Optional[list[dict]] = None) -> list[dict]:
    """Resolve replay entries against an optional library by id/filename."""
    by_id: dict[Any, dict] = {}
    by_name: dict[str, dict] = {}
    if library:
        for t in library:
            by_id[t.get("id")] = t
            fn = str(t.get("filename") or t.get("title") or "").lower()
            if fn:
                by_name[fn] = t
    tracks: list[dict] = []
    for e in entries:
        base = dict(e)
        tid = e.get("track_id")
        resolved = by_id.get(tid) if tid is not None else None
        if resolved is None and e.get("filename"):
            resolved = by_name.get(str(e["filename"]).lower())
        if resolved:
            merged = dict(resolved)
            merged["start_sec"] = e["start_sec"]
            tracks.append(merged)
        else:
            # Synthetic track for scoring / sim
            tracks.append(
                {
                    "id": tid,
                    "filename": e.get("filename") or str(tid),
                    "bpm": e.get("bpm") or 128.0,
                    "camelot": e.get("camelot") or e.get("key"),
                    "energy": e.get("energy", 0.5),
                    "start_sec": e["start_sec"],
                    "duration_sec": e.get("duration_sec") or 240.0,
                    "outro_sec": e.get("outro_sec"),
                }
            )
    return tracks
