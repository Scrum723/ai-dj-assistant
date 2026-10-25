"""C2 — Track analysis v2: synth WAVs, Camelot, XML, migration, lazy librosa."""
from __future__ import annotations

import sqlite3
import textwrap
from pathlib import Path

import pytest

from audio_analyzer import (
    all_camelot_codes,
    analyze_track,
    apply_rekordbox_import,
    fold_tempo,
    init_db,
    key_to_camelot,
    normalize_key_name,
    scan_directory,
    write_wav_click_track,
)
from rekordbox_xml import location_to_path, parse_collection


def test_no_toplevel_librosa_import():
    src = Path(__file__).resolve().parents[1] / "backend" / "audio_analyzer.py"
    text = src.read_text(encoding="utf-8")
    head, _, after = text.partition("def _load_librosa")
    assert "\nimport librosa" not in head
    assert "from librosa" not in head
    assert "import librosa" in after


def test_fold_tempo():
    assert abs(fold_tempo(60) - 120) < 1e-6
    assert abs(fold_tempo(240) - 120) < 1e-6
    assert 70 <= fold_tempo(174) <= 180


def test_all_24_camelot_codes():
    codes = all_camelot_codes()
    assert len(codes) == 24
    assert len(set(codes.values())) == 24
    assert codes["C major"] == "8B"
    assert codes["A minor"] == "8A"
    assert codes["F# major"] == "2B"
    assert codes["G# minor"] == "1A"
    for label, code in codes.items():
        root, mode = label.rsplit(" ", 1)
        assert key_to_camelot(root, mode) == code


def test_normalize_key_and_camelot_roundtrip():
    assert normalize_key_name("Am") == ("A", "minor")
    assert normalize_key_name("C#m") == ("C#", "minor")
    assert normalize_key_name("Bb") == ("A#", "major")
    assert normalize_key_name("8A") == ("A", "minor")
    assert normalize_key_name("10B") == ("D", "major")


@pytest.mark.parametrize("bpm", [120, 128, 174])
def test_click_fixture_bpm(tmp_path, bpm):
    pytest.importorskip("librosa")
    pytest.importorskip("numpy")
    wav = tmp_path / f"click_{bpm}.wav"
    write_wav_click_track(str(wav), bpm=bpm, duration_sec=12.0)
    features = analyze_track(str(wav))
    assert features is not None
    assert abs(features["bpm"] - bpm) <= 0.5, features["bpm"]


@pytest.mark.parametrize(
    "root,mode,expect_camelot",
    [
        ("C", "major", "8B"),
        ("A", "minor", "8A"),
        ("G", "major", "9B"),
    ],
)
def test_synth_key_camelot(tmp_path, root, mode, expect_camelot):
    pytest.importorskip("librosa")
    pytest.importorskip("numpy")
    wav = tmp_path / f"{root}_{mode}.wav"
    write_wav_click_track(str(wav), bpm=120, duration_sec=8.0, key_root=root, mode=mode)
    features = analyze_track(str(wav))
    assert features is not None
    assert features["camelot"] == expect_camelot
    assert features["mode"] == mode


def test_migration_adds_columns(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE tracks (id INTEGER PRIMARY KEY, filepath TEXT UNIQUE, "
        "filename TEXT, bpm REAL, key TEXT, energy REAL)"
    )
    conn.execute(
        "INSERT INTO tracks (filepath, filename, bpm, key, energy) VALUES (?,?,?,?,?)",
        ("/x/a.wav", "a.wav", 120.0, "C", 0.1),
    )
    conn.commit()
    conn.close()
    init_db(str(db))
    cols = {r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(tracks)")}
    for name in (
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
    ):
        assert name in cols
    row = sqlite3.connect(db).execute("SELECT filename, bpm FROM tracks").fetchone()
    assert row == ("a.wav", 120.0)


def test_rekordbox_xml_parse_and_import(tmp_path):
    wav_name = "fixture_track.wav"
    # Create a real path entry in DB first
    db = tmp_path / "library.db"
    init_db(str(db))
    audio = tmp_path / wav_name
    audio.write_bytes(b"RIFF")  # stub file; import does not re-read audio
    filepath = str(audio)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO tracks (filepath, filename, bpm, key, energy) VALUES (?,?,?,?,?)",
        (filepath, wav_name, 100.0, "C", 0.0),
    )
    conn.commit()
    conn.close()

    # Location uses forward-slash file URL
    loc = "file://localhost/" + filepath.replace("\\", "/")
    xml = tmp_path / "collection.xml"
    xml.write_text(
        textwrap.dedent(
            f"""\
            <?xml version="1.0" encoding="UTF-8"?>
            <DJ_PLAYLISTS Version="1.0.0">
              <PRODUCT Name="rekordbox" Version="6.0.0" Company="Pioneer DJ"/>
              <COLLECTION Entries="1">
                <TRACK TrackID="1" Name="Fixture" Artist="Test"
                       AverageBpm="128.00" Tonality="Am" TotalTime="120"
                       Location="{loc}">
                  <TEMPO Inizio="0.000" Bpm="128.00" Metro="4/4" Battito="1"/>
                  <POSITION_MARK Name="" Type="0" Start="0.000" Num="-1"/>
                </TRACK>
              </COLLECTION>
            </DJ_PLAYLISTS>
            """
        ),
        encoding="utf-8",
    )
    tracks = parse_collection(str(xml))
    assert len(tracks) == 1
    assert tracks[0]["AverageBpm"] == "128.00"
    assert tracks[0]["Tonality"] == "Am"
    assert len(tracks[0]["tempos"]) == 1

    n = apply_rekordbox_import(str(xml), db_path=str(db))
    assert n == 1
    row = sqlite3.connect(db).execute(
        "SELECT bpm, key, camelot, mode, source, beats_json, downbeats_json FROM tracks WHERE filename=?",
        (wav_name,),
    ).fetchone()
    assert row is not None
    bpm, key, camelot, mode, source, beats_json, downbeats_json = row
    assert abs(bpm - 128.0) < 0.01
    assert key == "Am"
    assert camelot == "8A"
    assert mode == "minor"
    assert source == "rekordbox"
    assert beats_json and "[" in beats_json
    assert downbeats_json and "[" in downbeats_json


def test_location_to_path_windows_and_posix():
    p = location_to_path("file://localhost/C:/Music/track.wav")
    assert "track.wav" in p
    assert p.endswith("track.wav")


def test_scan_reanalyze_cli_helpers(tmp_path):
    pytest.importorskip("librosa")
    pytest.importorskip("numpy")
    db = tmp_path / "lib.db"
    folder = tmp_path / "songs"
    folder.mkdir()
    wav = folder / "t120.wav"
    write_wav_click_track(str(wav), bpm=120, duration_sec=6.0)
    n = scan_directory(str(folder), reanalyze=False, db_path=str(db))
    assert n == 1
    n2 = scan_directory(str(folder), reanalyze=False, db_path=str(db))
    assert n2 == 0
    n3 = scan_directory(str(folder), reanalyze=True, db_path=str(db))
    assert n3 == 1
    row = sqlite3.connect(db).execute(
        "SELECT source, duration_sec, camelot FROM tracks"
    ).fetchone()
    assert row[0] == "librosa"
    assert row[1] and row[1] > 0
    assert row[2]
