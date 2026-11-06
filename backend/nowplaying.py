"""Now Playing service — pluggable sources for current track metadata.

Shared state shape:
  {artist, title, bpm, key, source, started_at, track_id?, art?}

Sources (settings.nowplaying.sources, default ["internal","wnp","file"]):
  internal — booth state["current_track"], bpm/key enriched from library.db
  wnp      — What's Now Playing GET /v1/last
  file     — text ("Artist - Title") or JSON file
  bridge   — external now_playing_bridge.py HTTP endpoint
  manual   — operator-set via set_manual() (POST /api/nowplaying in V2)

WNP docs (retrieved 2026-11-05):
  https://raw.githubusercontent.com/whatsnowplaying/whats-now-playing/main/docs/reference/api.md
  Default URL: http://127.0.0.1:8899/v1/last
  Typical fields: artist, title, album, genre, date, coverurl, bpm, key, …

Normalization mirrors visualizer js/nowplaying.js norm() (v3; absent in vendored v2):
  accepts flat {artist,title,…}, {track:{…},source}, and WNP /v1/last shapes.
  Dedupe key is artist|title (case-folded, stripped).

Cloud-safe: wnp/file/bridge report unavailable (never raise). Never crash the poll loop.

Env:
  NOWPLAYING_SOURCES, NOWPLAYING_WNP_URL, NOWPLAYING_FILE,
  NOWPLAYING_BRIDGE_URL, NOWPLAYING_POLL_SEC
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import urllib.request
from collections import deque
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_SOURCES = ["internal", "wnp", "file"]
DEFAULT_WNP_URL = "http://127.0.0.1:8899/v1/last"
DEFAULT_POLL_SEC = 2.0
DEFAULT_DEBOUNCE_SEC = 3.0
HISTORY_MAX = 20

TrackDict = dict[str, Any]
GetTrackFn = Callable[[], Optional[dict]]
OnChangeFn = Callable[[TrackDict], None]
ClockFn = Callable[[], float]


def _history_path() -> Path:
    raw = os.getenv("DATA_DIR")
    base = Path(raw) if raw else Path(__file__).resolve().parent
    try:
        base.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return base / "nowplaying.jsonl"


def _cloud() -> bool:
    return bool(os.getenv("RAILWAY_ENVIRONMENT"))


def dedupe_key(artist: Optional[str], title: Optional[str]) -> str:
    a = (artist or "").strip().casefold()
    t = (title or "").strip().casefold()
    return f"{a}|{t}"


def _empty_to_none(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, str) and not v.strip():
        return None
    return v


def _as_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_artist_title_text(text: str) -> tuple[Optional[str], Optional[str]]:
    line = (text or "").strip().splitlines()[0].strip() if text else ""
    if not line:
        return None, None
    # strip common audio extensions from a lone filename
    for ext in (".mp3", ".flac", ".wav", ".aiff", ".aif", ".m4a", ".aac"):
        if line.casefold().endswith(ext):
            line = line[: -len(ext)].strip()
            break
    if " - " in line:
        left, right = line.split(" - ", 1)
        return left.strip() or None, right.strip() or None
    return None, line or None


def norm(raw: Any, *, source: Optional[str] = None) -> Optional[TrackDict]:
    """Normalize WNP / visualizer / bridge / file payloads to shared state shape."""
    if raw is None:
        return None
    if isinstance(raw, str):
        artist, title = _parse_artist_title_text(raw)
        if not title and not artist:
            return None
        return {
            "artist": artist,
            "title": title,
            "bpm": None,
            "key": None,
            "source": source or "file",
            "started_at": None,
            "track_id": None,
            "art": None,
        }
    if not isinstance(raw, dict):
        return None

    data = raw
    src = source or _empty_to_none(raw.get("source"))
    # {track: {...}, source} envelope (visualizer / bridge)
    nested = raw.get("track")
    if isinstance(nested, dict) and (
        nested.get("artist") is not None
        or nested.get("title") is not None
        or nested.get("name") is not None
        or nested.get("filename") is not None
    ):
        data = nested
        src = src or source

    artist = _empty_to_none(data.get("artist"))
    title = _empty_to_none(
        data.get("title") or data.get("name") or data.get("song") or data.get("track_title")
    )
    filename = _empty_to_none(data.get("filename"))
    if (not artist or not title) and isinstance(filename, str):
        fa, ft = _parse_artist_title_text(filename)
        artist = artist or fa
        title = title or ft or filename

    if not artist and not title:
        return None

    art = _empty_to_none(
        data.get("art")
        or data.get("artwork")
        or data.get("artworkUrl")
        or data.get("coverurl")
        or data.get("cover")
        or data.get("cover_url")
    )
    track_id = data.get("track_id")
    if track_id is None:
        track_id = data.get("id")

    bpm = _as_float(data.get("bpm"))
    key = _empty_to_none(data.get("key") or data.get("camelot"))

    out: TrackDict = {
        "artist": artist,
        "title": title,
        "bpm": bpm,
        "key": key,
        "source": src or "unknown",
        "started_at": data.get("started_at"),
        "track_id": track_id,
        "art": art,
    }
    return out


def unavailable(source: str) -> TrackDict:
    return {
        "artist": None,
        "title": None,
        "bpm": None,
        "key": None,
        "source": source,
        "started_at": None,
        "track_id": None,
        "art": None,
        "unavailable": True,
    }


def _http_get_json(url: str, timeout: float = 2.0) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "DJBotBotty-nowplaying/1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    if not body:
        return None
    return json.loads(body.decode("utf-8", errors="replace"))


class NowPlaying:
    """Poll ordered sources; dedupe on artist|title; debounce track_change (default 3s)."""

    def __init__(
        self,
        *,
        get_internal_track: Optional[GetTrackFn] = None,
        library_db: Optional[str] = None,
        sources: Optional[list[str]] = None,
        wnp_url: str = DEFAULT_WNP_URL,
        file_path: str = "",
        bridge_url: str = "",
        poll_sec: float = DEFAULT_POLL_SEC,
        debounce_sec: float = DEFAULT_DEBOUNCE_SEC,
        clock: Optional[ClockFn] = None,
        http_get_json: Optional[Callable[[str], Any]] = None,
    ):
        self.get_internal_track = get_internal_track
        self.library_db = library_db
        self.sources = list(sources or DEFAULT_SOURCES)
        self.wnp_url = (wnp_url or DEFAULT_WNP_URL).rstrip("/")
        if self.wnp_url.endswith("/v1/last"):
            pass
        elif self.wnp_url.endswith("/v1"):
            self.wnp_url = self.wnp_url + "/last"
        elif "/v1/" not in self.wnp_url and not self.wnp_url.endswith("/last"):
            # allow base like http://127.0.0.1:8899
            self.wnp_url = self.wnp_url + "/v1/last"
        self.file_path = file_path or ""
        self.bridge_url = (bridge_url or "").strip()
        self.poll_sec = float(poll_sec or DEFAULT_POLL_SEC)
        self.debounce_sec = float(debounce_sec or DEFAULT_DEBOUNCE_SEC)
        self._clock: ClockFn = clock or time.time
        self._http_get_json = http_get_json or _http_get_json

        self._current: Optional[TrackDict] = None
        self._pending: Optional[TrackDict] = None
        self._pending_since: Optional[float] = None
        self._manual: Optional[TrackDict] = None
        self._listeners: list[OnChangeFn] = []
        self._stop = False
        self._last_change_at: Optional[float] = None
        self._history: deque[TrackDict] = deque(maxlen=HISTORY_MAX)
        self._load_history()

    # --- config helpers -------------------------------------------------

    @classmethod
    def from_settings(
        cls,
        settings: Optional[dict] = None,
        *,
        get_internal_track: Optional[GetTrackFn] = None,
        library_db: Optional[str] = None,
        clock: Optional[ClockFn] = None,
    ) -> "NowPlaying":
        cfg = dict((settings or {}).get("nowplaying") or {})
        sources = _sources_from_env(cfg.get("sources"))
        wnp_url = os.getenv("NOWPLAYING_WNP_URL") or cfg.get("wnp_url") or DEFAULT_WNP_URL
        file_path = os.getenv("NOWPLAYING_FILE") or cfg.get("file") or ""
        bridge_url = os.getenv("NOWPLAYING_BRIDGE_URL") or cfg.get("bridge_url") or ""
        poll_raw = os.getenv("NOWPLAYING_POLL_SEC")
        poll_sec = float(poll_raw) if poll_raw not in (None, "") else float(cfg.get("poll_sec") or DEFAULT_POLL_SEC)
        debounce_sec = float(cfg.get("debounce_sec") or DEFAULT_DEBOUNCE_SEC)
        return cls(
            get_internal_track=get_internal_track,
            library_db=library_db,
            sources=sources,
            wnp_url=str(wnp_url),
            file_path=str(file_path or ""),
            bridge_url=str(bridge_url or ""),
            poll_sec=poll_sec,
            debounce_sec=debounce_sec,
            clock=clock,
        )

    def on_track_change(self, fn: OnChangeFn) -> None:
        self._listeners.append(fn)

    def state(self) -> dict:
        cur = self._current
        if not cur:
            return {}
        out = {k: v for k, v in cur.items() if k != "unavailable"}
        return out

    def health(self) -> dict:
        """Compact status for /healthz: source, ok, last_change_age."""
        cur = self._current
        source = (cur or {}).get("source") if cur else None
        age = None
        if self._last_change_at is not None:
            age = max(0.0, float(self._clock()) - float(self._last_change_at))
        return {
            "source": source,
            "ok": True,
            "last_change_age": age,
        }

    def current(self) -> Optional[TrackDict]:
        return None if not self._current else dict(self._current)

    def history(self, limit: int = HISTORY_MAX) -> list[TrackDict]:
        """Last N committed tracks (newest last)."""
        n = max(1, min(int(limit or HISTORY_MAX), HISTORY_MAX))
        items = list(self._history)
        return [dict(x) for x in items[-n:]]

    def _load_history(self) -> None:
        path = _history_path()
        if not path.is_file():
            return
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            loaded: list[TrackDict] = []
            for line in lines[-HISTORY_MAX:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if isinstance(row, dict) and (row.get("title") or row.get("artist")):
                    loaded.append(row)
            self._history.clear()
            for row in loaded[-HISTORY_MAX:]:
                self._history.append(row)
        except Exception:
            logger.debug("nowplaying history load failed", exc_info=True)

    def _append_history(self, track: TrackDict) -> None:
        if not track or track.get("unavailable"):
            return
        if not (track.get("title") or track.get("artist")):
            return
        entry = {k: v for k, v in track.items() if k != "unavailable"}
        self._history.append(dict(entry))
        try:
            path = _history_path()
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            logger.debug("nowplaying history append failed", exc_info=True)

    def set_manual(self, raw: Any) -> Optional[TrackDict]:
        n = norm(raw, source="manual")
        if n:
            n["source"] = "manual"
            if n.get("started_at") is None:
                n["started_at"] = self._clock()
        self._manual = n
        return n

    def apply_manual_now(self, raw: Any) -> Optional[TrackDict]:
        """Operator set: normalize, store as manual, commit immediately (no debounce)."""
        n = self.set_manual(raw)
        if not n:
            return None
        self._commit(n, self._clock())
        return dict(n)

    def clear_manual(self, *, clear_current: bool = True) -> Optional[TrackDict]:
        """Clear manual override. If current was manual, drop it and notify listeners with {}."""
        prev = dict(self._current) if self._current else None
        self._manual = None
        if clear_current and self._current and self._current.get("source") == "manual":
            self._current = None
            self._pending = None
            self._pending_since = None
            empty: TrackDict = {}
            for fn in list(self._listeners):
                try:
                    fn(empty)
                except Exception:
                    logger.exception("nowplaying track_change listener failed")
            return prev
        return None

    # --- sources --------------------------------------------------------

    def read_internal(self) -> TrackDict:
        try:
            raw = self.get_internal_track() if self.get_internal_track else None
            if not raw:
                return unavailable("internal")
            n = norm(raw, source="internal")
            if not n:
                return unavailable("internal")
            n["source"] = "internal"
            self._enrich_library(n, raw)
            return n
        except Exception:
            logger.exception("nowplaying internal source failed")
            return unavailable("internal")

    def read_wnp(self) -> TrackDict:
        if _cloud() or not self.wnp_url:
            return unavailable("wnp")
        try:
            data = self._http_get_json(self.wnp_url)
            n = norm(data, source="wnp")
            if not n:
                return unavailable("wnp")
            n["source"] = "wnp"
            return n
        except Exception as exc:
            logger.debug("nowplaying wnp unavailable: %s", exc)
            return unavailable("wnp")

    def read_file(self) -> TrackDict:
        path = (self.file_path or "").strip()
        if _cloud() or not path:
            return unavailable("file")
        try:
            p = Path(path)
            if not p.is_file():
                return unavailable("file")
            text = p.read_text(encoding="utf-8", errors="replace").strip()
            if not text:
                return unavailable("file")
            if text.startswith("{") or text.startswith("["):
                data = json.loads(text)
                if isinstance(data, list):
                    data = data[0] if data else None
                n = norm(data, source="file")
            else:
                n = norm(text, source="file")
            if not n:
                return unavailable("file")
            n["source"] = "file"
            return n
        except Exception as exc:
            logger.debug("nowplaying file unavailable: %s", exc)
            return unavailable("file")

    def read_bridge(self) -> TrackDict:
        if _cloud() or not self.bridge_url:
            return unavailable("bridge")
        try:
            data = self._http_get_json(self.bridge_url)
            n = norm(data, source="bridge")
            if not n:
                return unavailable("bridge")
            n["source"] = "bridge"
            return n
        except Exception as exc:
            logger.debug("nowplaying bridge unavailable: %s", exc)
            return unavailable("bridge")

    def read_manual(self) -> TrackDict:
        if self._manual and not self._manual.get("unavailable"):
            return dict(self._manual)
        return unavailable("manual")

    def _enrich_library(self, n: TrackDict, raw: dict) -> None:
        if n.get("bpm") is not None and n.get("key") is not None:
            return
        db = self.library_db
        if not db:
            # fall back to fields already on raw track
            if n.get("bpm") is None:
                n["bpm"] = _as_float(raw.get("bpm"))
            if n.get("key") is None:
                n["key"] = _empty_to_none(raw.get("key") or raw.get("camelot"))
            return
        try:
            tid = raw.get("id") or n.get("track_id")
            filename = raw.get("filename")
            with sqlite3.connect(db) as conn:
                conn.row_factory = sqlite3.Row
                row = None
                if tid is not None:
                    row = conn.execute(
                        "SELECT id, bpm, key, filename FROM tracks WHERE id = ?",
                        (tid,),
                    ).fetchone()
                if row is None and filename:
                    row = conn.execute(
                        "SELECT id, bpm, key, filename FROM tracks WHERE filename = ?",
                        (filename,),
                    ).fetchone()
                if row is not None:
                    if n.get("bpm") is None:
                        n["bpm"] = _as_float(row["bpm"])
                    if n.get("key") is None:
                        n["key"] = _empty_to_none(row["key"])
                    if n.get("track_id") is None:
                        n["track_id"] = row["id"]
        except Exception:
            logger.debug("nowplaying library enrich failed", exc_info=True)
            if n.get("bpm") is None:
                n["bpm"] = _as_float(raw.get("bpm"))
            if n.get("key") is None:
                n["key"] = _empty_to_none(raw.get("key") or raw.get("camelot"))

    def poll_once(self) -> Optional[TrackDict]:
        """Try sources in order; return first available normalized track (or None)."""
        readers = {
            "internal": self.read_internal,
            "wnp": self.read_wnp,
            "file": self.read_file,
            "bridge": self.read_bridge,
            "manual": self.read_manual,
        }
        for name in self.sources:
            reader = readers.get(name)
            if not reader:
                continue
            try:
                result = reader()
            except Exception:
                logger.exception("nowplaying source %s crashed", name)
                continue
            if result and not result.get("unavailable") and (result.get("artist") or result.get("title")):
                return result
        return None

    # --- dedupe / debounce ----------------------------------------------

    def apply_candidate(self, candidate: Optional[TrackDict], now: Optional[float] = None) -> bool:
        """Apply a polled track. Returns True if a track_change was emitted.

        Dedupe on artist|title. A real change only fires after debounce_sec
        of a stable new key (cue-preview flicker protection).
        """
        now = self._clock() if now is None else float(now)
        if candidate is None or candidate.get("unavailable"):
            self._pending = None
            self._pending_since = None
            return False

        new_key = dedupe_key(candidate.get("artist"), candidate.get("title"))
        if not new_key or new_key == "|":
            return False

        cur_key = (
            dedupe_key(self._current.get("artist"), self._current.get("title"))
            if self._current
            else None
        )
        if new_key == cur_key:
            self._pending = None
            self._pending_since = None
            # refresh mutable fields without emitting
            if self._current is not None:
                for field in ("bpm", "key", "art", "track_id", "source"):
                    if candidate.get(field) is not None:
                        self._current[field] = candidate.get(field)
            return False

        pend_key = (
            dedupe_key(self._pending.get("artist"), self._pending.get("title"))
            if self._pending
            else None
        )
        if new_key != pend_key:
            self._pending = dict(candidate)
            self._pending_since = now
            # First observation ever: still debounce unless nothing current
            if self._current is None and self.debounce_sec <= 0:
                return self._commit(candidate, now)
            return False

        assert self._pending_since is not None
        if (now - self._pending_since) < self.debounce_sec:
            # keep freshest pending payload
            self._pending = dict(candidate)
            return False

        return self._commit(candidate, now)

    def _commit(self, candidate: TrackDict, now: float) -> bool:
        track = dict(candidate)
        if track.get("started_at") is None:
            track["started_at"] = now
        self._current = track
        self._last_change_at = now
        self._pending = None
        self._pending_since = None
        self._append_history(track)
        for fn in list(self._listeners):
            try:
                fn(dict(track))
            except Exception:
                logger.exception("nowplaying track_change listener failed")
        return True

    def tick(self, now: Optional[float] = None) -> bool:
        """One poll + apply. Returns True if track_change emitted."""
        try:
            cand = self.poll_once()
            return self.apply_candidate(cand, now=now)
        except Exception:
            logger.exception("nowplaying tick failed")
            return False

    async def run_poll_loop(self) -> None:
        """Asyncio task body — never raises out."""
        import asyncio

        self._stop = False
        while not self._stop:
            try:
                self.tick()
            except Exception:
                logger.exception("nowplaying poll loop tick failed")
            try:
                await asyncio.sleep(max(0.2, float(self.poll_sec)))
            except asyncio.CancelledError:
                raise

    def stop(self) -> None:
        self._stop = True


def _sources_from_env(cfg_sources: Any) -> list[str]:
    env_raw = os.getenv("NOWPLAYING_SOURCES")
    if env_raw is not None and env_raw.strip() != "":
        return [s.strip() for s in env_raw.split(",") if s.strip()]
    if isinstance(cfg_sources, list) and cfg_sources:
        return [str(s).strip() for s in cfg_sources if str(s).strip()]
    return list(DEFAULT_SOURCES)
