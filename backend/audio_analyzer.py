"""Track analysis into library.db. librosa/numpy are lazy — cloud boots without them."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import wave
from typing import Any, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "library.db")
DEFAULT_DIR = os.path.expanduser("~/Desktop/Finished Songs")

KEYS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
KEY_ALIASES = {
    "Db": "C#",
    "Eb": "D#",
    "Gb": "F#",
    "Ab": "G#",
    "Bb": "A#",
    "Dbm": "C#m",
    "Ebm": "D#m",
    "Gbm": "F#m",
    "Abm": "G#m",
    "Bbm": "A#m",
}

# Camelot: major = B codes, minor = A codes (standard Mixed In Key wheel)
CAMELOT_MAJOR = {
    "C": "8B",
    "G": "9B",
    "D": "10B",
    "A": "11B",
    "E": "12B",
    "B": "1B",
    "F#": "2B",
    "C#": "3B",
    "G#": "4B",
    "D#": "5B",
    "A#": "6B",
    "F": "7B",
}
CAMELOT_MINOR = {
    "A": "8A",
    "E": "9A",
    "B": "10A",
    "F#": "11A",
    "C#": "12A",
    "G#": "1A",
    "D#": "2A",
    "A#": "3A",
    "F": "4A",
    "C": "5A",
    "G": "6A",
    "D": "7A",
}

KRUMHANSL_MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
KRUMHANSL_MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]

NEW_COLUMNS = (
    ("camelot", "TEXT"),
    ("mode", "TEXT"),
    ("beats_json", "TEXT"),
    ("downbeats_json", "TEXT"),
    ("phrases_json", "TEXT"),
    ("energy_curve_json", "TEXT"),
    ("intro_sec", "REAL"),
    ("outro_sec", "REAL"),
    ("duration_sec", "REAL"),
    ("source", "TEXT"),
)


def _load_librosa():
    """Lazy import — never raise at module import time."""
    import librosa  # type: ignore
    import numpy as np  # type: ignore

    return librosa, np


def normalize_key_name(raw: str) -> tuple[Optional[str], Optional[str]]:
    """Return (root, mode) from strings like 'Am', 'C#m', 'F major', '8A'."""
    if not raw:
        return None, None
    s = raw.strip()
    if len(s) >= 2 and s[-1] in "ABab" and s[:-1].isdigit():
        # Camelot code → key
        code = s.upper() if s[-1] in "ab" else s
        code = code[:-1] + code[-1].upper()
        for root, c in CAMELOT_MAJOR.items():
            if c == code:
                return root, "major"
        for root, c in CAMELOT_MINOR.items():
            if c == code:
                return root, "minor"
        return None, None
    s = s.replace("major", "").replace("minor", "m").replace("Maj", "").replace("Min", "m")
    s = s.replace(" ", "")
    s = KEY_ALIASES.get(s, s)
    if s.endswith("m") and len(s) > 1:
        root = s[:-1]
        root = KEY_ALIASES.get(root, root)
        if root in KEYS:
            return root, "minor"
    else:
        root = KEY_ALIASES.get(s, s)
        if root in KEYS:
            return root, "major"
    return None, None


def key_to_camelot(root: str, mode: str) -> Optional[str]:
    if mode == "minor":
        return CAMELOT_MINOR.get(root)
    return CAMELOT_MAJOR.get(root)


def all_camelot_codes() -> dict[str, str]:
    """Map 'C major' / 'A minor' → Camelot for tests."""
    out: dict[str, str] = {}
    for root, code in CAMELOT_MAJOR.items():
        out[f"{root} major"] = code
    for root, code in CAMELOT_MINOR.items():
        out[f"{root} minor"] = code
    return out


def fold_tempo(bpm: float, lo: float = 70.0, hi: float = 180.0) -> float:
    """Fold tempo into [lo, hi] via half/double."""
    if bpm <= 0:
        return bpm
    while bpm < lo:
        bpm *= 2.0
    while bpm > hi:
        bpm /= 2.0
    return float(bpm)


def _estimate_bpm(y, sr, librosa, np) -> float:
    """BPM from onset-env peaks (refined), else librosa.beat_track."""
    hop = 256
    try:
        onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
        peaks = librosa.util.peak_pick(
            onset_env,
            pre_max=3,
            post_max=3,
            pre_avg=3,
            post_avg=5,
            delta=0.2,
            wait=2,
        )
        if len(peaks) >= 4:
            times = librosa.frames_to_time(peaks, sr=sr, hop_length=hop)
            ioi = np.diff(times)
            ioi = ioi[(ioi > 0.14) & (ioi < 1.05)]
            if len(ioi) >= 3:
                med = float(np.median(ioi))
                rough = 60.0 / med
                # Refine against onset envelope energy at candidate grid
                best, best_score = rough, -1.0
                for cand in np.linspace(max(rough - 4, 50), rough + 4, 81):
                    interval = 60.0 / float(cand)
                    n_beats = int(len(y) / sr / interval)
                    if n_beats < 4:
                        continue
                    grid = np.arange(n_beats) * interval
                    frames = librosa.time_to_frames(grid, sr=sr, hop_length=hop)
                    frames = frames[(frames >= 0) & (frames < len(onset_env))]
                    if len(frames) < 4:
                        continue
                    score = float(np.mean(onset_env[frames]))
                    if score > best_score:
                        best_score, best = score, float(cand)
                return best
        tempo = float(librosa.feature.tempo(onset_envelope=onset_env, sr=sr, hop_length=hop)[0])
        return tempo
    except Exception:
        pass
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    return float(tempo[0] if hasattr(tempo, "__len__") else tempo)


def estimate_key_from_chroma(chroma_mean) -> tuple[str, str, str]:
    """Krumhansl-Schmuckler on 12-bin chroma mean. Returns (key, mode, camelot)."""
    np = __import__("numpy")
    x = np.asarray(chroma_mean, dtype=float).reshape(12)
    if float(np.sum(x)) <= 0:
        return "C", "major", "8B"
    x = x - np.mean(x)
    best_r, best_root, best_mode = -2.0, 0, "major"
    maj = np.asarray(KRUMHANSL_MAJOR, dtype=float)
    minr = np.asarray(KRUMHANSL_MINOR, dtype=float)
    maj = maj - np.mean(maj)
    minr = minr - np.mean(minr)
    for i in range(12):
        r_maj = float(np.corrcoef(x, np.roll(maj, i))[0, 1])
        r_min = float(np.corrcoef(x, np.roll(minr, i))[0, 1])
        if r_maj > best_r:
            best_r, best_root, best_mode = r_maj, i, "major"
        if r_min > best_r:
            best_r, best_root, best_mode = r_min, i, "minor"
    root = KEYS[best_root]
    camelot = key_to_camelot(root, best_mode) or "8B"
    return root, best_mode, camelot


def _column_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("PRAGMA table_info(tracks)").fetchall()
    return {r[1] for r in rows}


def init_db(db_path: Optional[str] = None) -> str:
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filepath TEXT UNIQUE,
            filename TEXT,
            bpm REAL,
            key TEXT,
            energy REAL
        )
        """
    )
    existing = _column_names(conn)
    for name, coltype in NEW_COLUMNS:
        if name not in existing:
            c.execute(f"ALTER TABLE tracks ADD COLUMN {name} {coltype}")
            logger.info("Migrated column: %s", name)
    conn.commit()
    conn.close()
    return path


def _phrases_from_downbeats(downbeats: list[float], bars_opts=(16, 32)) -> list[dict]:
    """Group consecutive downbeats into 16/32-bar phrases."""
    if len(downbeats) < 2:
        return []
    phrases: list[dict] = []
    i = 0
    n = len(downbeats)
    while i < n - 1:
        # Prefer 32-bar when enough bars remain, else 16, else remainder
        remaining = n - i
        length = 32 if remaining >= 32 else (16 if remaining >= 16 else max(1, remaining - 1))
        if length not in bars_opts and remaining >= 16:
            length = 16
        end_i = min(i + length, n - 1)
        phrases.append(
            {
                "start": float(downbeats[i]),
                "end": float(downbeats[end_i]),
                "bars": int(end_i - i),
            }
        )
        i = end_i
        if i >= n - 1:
            break
    return phrases


def _intro_outro(rms_curve, times, threshold_ratio: float = 0.25) -> tuple[float, float]:
    """Intro ends when energy exceeds threshold; outro starts when it drops near end."""
    np = __import__("numpy")
    if len(rms_curve) == 0:
        return 0.0, float(times[-1]) if len(times) else 0.0
    peak = float(np.max(rms_curve)) or 1.0
    thr = peak * threshold_ratio
    intro = 0.0
    for t, e in zip(times, rms_curve):
        if float(e) >= thr:
            intro = float(t)
            break
    outro = float(times[-1])
    for t, e in zip(reversed(list(times)), reversed(list(rms_curve))):
        if float(e) >= thr:
            outro = float(t)
            break
    return intro, outro


def analyze_track(filepath: str) -> Optional[dict[str, Any]]:
    try:
        librosa, np = _load_librosa()
    except ImportError as e:
        logger.error("librosa/numpy required for analysis: %s", e)
        return None
    try:
        logger.info("Analyzing %s...", filepath)
        y, sr = librosa.load(filepath, sr=22050, mono=True)
        duration = float(librosa.get_duration(y=y, sr=sr))
        # Prefer regular onset IOI (accurate on click/grid tracks), fall back to beat_track
        bpm_raw = _estimate_bpm(y, sr, librosa, np)
        bpm = fold_tempo(bpm_raw)
        _, beat_frames = librosa.beat.beat_track(y=y, sr=sr, bpm=bpm, units="frames")
        beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()
        # Downbeats: every 4th beat (4/4), align to strongest early beat
        downbeats = beat_times[0::4] if beat_times else []
        phrases = _phrases_from_downbeats([float(t) for t in downbeats])

        rms = librosa.feature.rms(y=y)[0]
        energy = float(np.mean(rms))
        # 8-bar energy curve: hop ≈ 8 bars at estimated bpm
        bar_sec = (60.0 / bpm) * 4.0 if bpm > 0 else 2.0
        window = max(bar_sec * 8.0, 1.0)
        n_frames = len(rms)
        frame_times = librosa.frames_to_time(np.arange(n_frames), sr=sr)
        curve: list[dict] = []
        t0 = 0.0
        while t0 < duration:
            t1 = min(t0 + window, duration)
            mask = (frame_times >= t0) & (frame_times < t1)
            val = float(np.mean(rms[mask])) if np.any(mask) else 0.0
            curve.append({"t": round(t0, 3), "energy": val})
            t0 = t1

        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        chroma_mean = np.mean(chroma, axis=1)
        root, mode, camelot = estimate_key_from_chroma(chroma_mean)
        key_label = root if mode == "major" else f"{root}m"

        intro_sec, outro_sec = _intro_outro(rms, frame_times)

        return {
            "bpm": bpm,
            "energy": energy,
            "key": key_label,
            "mode": mode,
            "camelot": camelot,
            "beats_json": json.dumps([round(float(t), 4) for t in beat_times]),
            "downbeats_json": json.dumps([round(float(t), 4) for t in downbeats]),
            "phrases_json": json.dumps(phrases),
            "energy_curve_json": json.dumps(curve),
            "intro_sec": intro_sec,
            "outro_sec": outro_sec,
            "duration_sec": duration,
            "source": "librosa",
        }
    except Exception as e:
        logger.error("Error analyzing %s: %s", filepath, e)
        return None


def upsert_track(conn: sqlite3.Connection, filepath: str, features: dict, *, source: Optional[str] = None) -> None:
    filename = os.path.basename(filepath)
    src = source or features.get("source") or "librosa"
    cols = [
        "filepath",
        "filename",
        "bpm",
        "key",
        "energy",
        "camelot",
        "mode",
        "beats_json",
        "downbeats_json",
        "phrases_json",
        "energy_curve_json",
        "intro_sec",
        "outro_sec",
        "duration_sec",
        "source",
    ]
    values = [
        filepath,
        filename,
        features.get("bpm"),
        features.get("key"),
        features.get("energy"),
        features.get("camelot"),
        features.get("mode"),
        features.get("beats_json"),
        features.get("downbeats_json"),
        features.get("phrases_json"),
        features.get("energy_curve_json"),
        features.get("intro_sec"),
        features.get("outro_sec"),
        features.get("duration_sec"),
        src,
    ]
    placeholders = ",".join("?" * len(cols))
    updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "filepath")
    conn.execute(
        f"INSERT INTO tracks ({','.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(filepath) DO UPDATE SET {updates}",
        values,
    )


def scan_directory(directory: str, *, reanalyze: bool = False, db_path: Optional[str] = None) -> int:
    if not os.path.isdir(directory):
        logger.error("Directory does not exist: %s", directory)
        return 0
    path = init_db(db_path)
    conn = sqlite3.connect(path)
    valid_extensions = (".mp3", ".wav", ".flac", ".aiff", ".m4a")
    scanned = 0
    for root, _dirs, files in os.walk(directory):
        for file in files:
            if not file.lower().endswith(valid_extensions):
                continue
            filepath = os.path.join(root, file)
            if not reanalyze:
                row = conn.execute("SELECT id FROM tracks WHERE filepath = ?", (filepath,)).fetchone()
                if row:
                    logger.info("Skipping already analyzed track: %s", file)
                    continue
            features = analyze_track(filepath)
            if not features:
                continue
            upsert_track(conn, filepath, features)
            conn.commit()
            scanned += 1
            logger.info(
                "Saved %s: %.1f BPM, Key: %s (%s)",
                file,
                features["bpm"],
                features["key"],
                features.get("camelot"),
            )
    conn.close()
    logger.info("Scan complete. Tracks written: %s", scanned)
    return scanned


def write_wav_click_track(
    path: str,
    bpm: float,
    duration_sec: float = 8.0,
    sr: int = 22050,
    *,
    key_root: str = "C",
    mode: str = "major",
) -> None:
    """Synthesise a click + tonal WAV for tests (stdlib wave + numpy)."""
    import numpy as np  # type: ignore

    n = int(sr * duration_sec)
    t = np.arange(n) / sr
    y = np.zeros(n, dtype=np.float32)
    # Sharp clicks on beats (exact sample grid)
    interval = 60.0 / bpm
    beat_t = 0.0
    click_len = max(int(0.008 * sr), 2)
    while beat_t < duration_sec - 1e-9:
        i0 = int(round(beat_t * sr))
        i1 = min(i0 + click_len, n)
        if 0 <= i0 < n:
            env = np.linspace(1.0, 0.0, i1 - i0)
            y[i0:i1] += env
        beat_t += interval
    # Tonal drone: C4 = 261.626 Hz as root 0
    root_idx = KEYS.index(key_root)
    if mode == "minor":
        intervals = [0, 3, 7]
    else:
        intervals = [0, 4, 7]
    for iv in intervals:
        freq = 261.626 * (2.0 ** ((root_idx + iv) / 12.0))
        y += 0.2 * np.sin(2 * np.pi * freq * t)
    peak = float(np.max(np.abs(y))) or 1.0
    y = (y / peak * 0.95).astype(np.float32)
    pcm = (y * 32767).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def apply_rekordbox_import(xml_path: str, db_path: Optional[str] = None) -> int:
    from rekordbox_xml import parse_collection, location_to_path

    path = init_db(db_path)
    tracks = parse_collection(xml_path)
    conn = sqlite3.connect(path)
    updated = 0
    for tr in tracks:
        filepath = location_to_path(tr.get("Location") or "")
        if not filepath:
            continue
        # Prefer matching existing by filepath basename if absolute missing
        row = conn.execute("SELECT id, filepath FROM tracks WHERE filepath = ?", (filepath,)).fetchone()
        if not row:
            base = os.path.basename(filepath)
            row = conn.execute(
                "SELECT id, filepath FROM tracks WHERE filename = ? OR filepath LIKE ?",
                (base, f"%{base}"),
            ).fetchone()
            if row:
                filepath = row[1]
            else:
                # Insert new stub so XML can populate
                pass
        features = _features_from_rekordbox(tr)
        upsert_track(conn, filepath, features, source="rekordbox")
        updated += 1
    conn.commit()
    conn.close()
    logger.info("Rekordbox XML import: %s tracks (source=rekordbox)", updated)
    return updated


def _features_from_rekordbox(tr: dict) -> dict[str, Any]:
    bpm = float(tr.get("AverageBpm") or 0) or None
    if bpm:
        bpm = fold_tempo(bpm)
    tonality = tr.get("Tonality") or ""
    root, mode = normalize_key_name(tonality)
    key_label = None
    camelot = None
    if root and mode:
        key_label = root if mode == "major" else f"{root}m"
        camelot = key_to_camelot(root, mode)
    tempos = tr.get("tempos") or []
    beats: list[float] = []
    downbeats: list[float] = []
    if tempos and bpm:
        # Expand TEMPO Inizio + Battito into a simple beat grid for duration
        duration = float(tr.get("TotalTime") or 0) or None
        for tempo in tempos:
            start = float(tempo.get("Inizio") or 0)
            local_bpm = float(tempo.get("Bpm") or bpm or 0)
            battito = int(float(tempo.get("Battito") or 1))
            if local_bpm <= 0:
                continue
            interval = 60.0 / local_bpm
            # First downbeat: back up (battito-1) beats from Inizio
            first_down = start - (battito - 1) * interval
            t = first_down
            end = duration if duration else start + 60.0
            while t < end:
                if t >= 0:
                    beats.append(round(t, 4))
                    # downbeat every 4
                    beat_num = round((t - first_down) / interval) % 4
                    if beat_num == 0:
                        downbeats.append(round(t, 4))
                t += interval
            break  # use first tempo region for grid
    duration_sec = float(tr.get("TotalTime") or 0) or None
    phrases = _phrases_from_downbeats(downbeats) if downbeats else []
    return {
        "bpm": bpm,
        "key": key_label,
        "energy": None,
        "mode": mode,
        "camelot": camelot,
        "beats_json": json.dumps(beats) if beats else None,
        "downbeats_json": json.dumps(downbeats) if downbeats else None,
        "phrases_json": json.dumps(phrases) if phrases else None,
        "energy_curve_json": None,
        "intro_sec": None,
        "outro_sec": None,
        "duration_sec": duration_sec,
        "source": "rekordbox",
    }


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Analyze a folder of tracks into library.db")
    parser.add_argument(
        "directory",
        nargs="?",
        default=DEFAULT_DIR,
        help=f"Folder to scan (default: {DEFAULT_DIR})",
    )
    parser.add_argument("--reanalyze", action="store_true", help="Re-analyze existing rows")
    parser.add_argument("--rekordbox-xml", dest="rekordbox_xml", metavar="PATH", help="Import rekordbox collection XML")
    parser.add_argument("--db", dest="db_path", default=None, help="Override library.db path")
    args = parser.parse_args(argv)
    init_db(args.db_path)
    print(f"Database: {args.db_path or DB_PATH}")
    if args.rekordbox_xml:
        print(f"Importing rekordbox XML: {args.rekordbox_xml}")
        apply_rekordbox_import(args.rekordbox_xml, db_path=args.db_path)
    if args.directory:
        print(f"Scanning: {args.directory} (reanalyze={args.reanalyze})")
        scan_directory(args.directory, reanalyze=args.reanalyze, db_path=args.db_path)


if __name__ == "__main__":
    main()
