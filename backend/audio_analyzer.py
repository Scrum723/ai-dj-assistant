import argparse
import logging
import os
import sqlite3

import librosa
import numpy as np

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "library.db")
DEFAULT_DIR = os.path.expanduser("~/Desktop/Finished Songs")


def init_db():
    conn = sqlite3.connect(DB_PATH)
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
    conn.commit()
    conn.close()


def analyze_track(filepath):
    try:
        logger.info("Analyzing %s...", filepath)
        y, sr = librosa.load(filepath, sr=22050, duration=120)
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        bpm = tempo[0] if isinstance(tempo, (list, np.ndarray)) else tempo
        rms = librosa.feature.rms(y=y)
        energy = np.mean(rms)
        chroma = librosa.feature.chroma_stft(y=y, sr=sr)
        key_idx = int(np.argmax(np.sum(chroma, axis=1)))
        keys = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        return {
            "bpm": float(bpm),
            "energy": float(energy),
            "key": keys[key_idx],
        }
    except Exception as e:
        logger.error("Error analyzing %s: %s", filepath, e)
        return None


def scan_directory(directory):
    if not os.path.isdir(directory):
        logger.error("Directory does not exist: %s", directory)
        return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    valid_extensions = (".mp3", ".wav", ".flac", ".aiff", ".m4a")
    scanned = 0
    for root, _dirs, files in os.walk(directory):
        for file in files:
            if not file.lower().endswith(valid_extensions):
                continue
            filepath = os.path.join(root, file)
            c.execute("SELECT id FROM tracks WHERE filepath = ?", (filepath,))
            if c.fetchone():
                logger.info("Skipping already analyzed track: %s", file)
                continue
            features = analyze_track(filepath)
            if not features:
                continue
            c.execute(
                "INSERT INTO tracks (filepath, filename, bpm, key, energy) VALUES (?, ?, ?, ?, ?)",
                (filepath, file, features["bpm"], features["key"], features["energy"]),
            )
            conn.commit()
            scanned += 1
            logger.info("Saved %s: %.1f BPM, Key: %s", file, features["bpm"], features["key"])
    conn.close()
    logger.info("Scan complete. New tracks added: %s", scanned)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze a folder of tracks into library.db")
    parser.add_argument(
        "directory",
        nargs="?",
        default=DEFAULT_DIR,
        help=f"Folder to scan (default: {DEFAULT_DIR})",
    )
    args = parser.parse_args()
    init_db()
    print(f"Database: {DB_PATH}")
    print(f"Scanning: {args.directory}")
    scan_directory(args.directory)
