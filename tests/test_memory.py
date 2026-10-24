"""Viewer memory upsert/lookup and restart persistence."""

from __future__ import annotations

import time

from memory import ViewerMemory, ordinal


def test_ordinal():
    assert ordinal(1) == "1st"
    assert ordinal(2) == "2nd"
    assert ordinal(3) == "3rd"
    assert ordinal(4) == "4th"
    assert ordinal(11) == "11th"
    assert ordinal(21) == "21st"
    assert ordinal(5) == "5th"


def test_touch_insert_and_get(tmp_path):
    mem = ViewerMemory(db_path=tmp_path / "halo.db")
    rec, returning = mem.touch("twitch", "BassLover")
    assert returning is False
    assert rec.visits == 1
    got = mem.get("twitch", "BassLover")
    assert got is not None
    assert got.visits == 1
    assert got.handle == "BassLover"


def test_touch_same_day_no_extra_visit(tmp_path):
    mem = ViewerMemory(db_path=tmp_path / "halo.db")
    mem.touch("chat", "Fan")
    rec, returning = mem.touch("chat", "Fan")
    assert returning is False
    assert rec.visits == 1


def test_touch_new_day_increments_and_returning(tmp_path):
    mem = ViewerMemory(db_path=tmp_path / "halo.db")
    mem.touch("youtube", "Regular")
    with mem._connect() as conn:
        conn.execute(
            "UPDATE viewers SET last_seen = ? WHERE platform = ? AND handle = ?",
            (time.time() - 86400 * 3, "youtube", "Regular"),
        )
        conn.commit()
    rec, returning = mem.touch("youtube", "Regular")
    assert returning is True
    assert rec.visits == 2


def test_visits_survive_reopen(tmp_path):
    path = tmp_path / "halo.db"
    mem1 = ViewerMemory(db_path=path)
    mem1.touch("chat", "Loyal")
    with mem1._connect() as conn:
        conn.execute(
            "UPDATE viewers SET last_seen = ?, visits = 4 WHERE platform = ? AND handle = ?",
            (time.time() - 86400, "chat", "Loyal"),
        )
        conn.commit()
    mem1.touch("chat", "Loyal")  # 5th visit

    mem2 = ViewerMemory(db_path=path)
    got = mem2.get("chat", "Loyal")
    assert got is not None
    assert got.visits == 5


def test_remember_request(tmp_path):
    mem = ViewerMemory(db_path=tmp_path / "halo.db")
    mem.remember_request("chat", "Fan", "Mad World")
    got = mem.get("chat", "Fan")
    assert got is not None
    assert got.last_request == "Mad World"
    assert "5th" not in got.memory_line()  # 1st visit
    assert "1st visit" in got.memory_line()
    assert "last asked for Mad World" in got.memory_line()


def test_memory_line_with_visits(tmp_path):
    mem = ViewerMemory(db_path=tmp_path / "halo.db")
    mem.touch("chat", "Fan")
    with mem._connect() as conn:
        conn.execute(
            "UPDATE viewers SET visits = 5, last_request = ? WHERE platform = ? AND handle = ?",
            ("Bloodrave", "chat", "Fan"),
        )
        conn.commit()
    line = mem.get("chat", "Fan").memory_line()
    assert line == "Viewer memory: 5th visit, last asked for Bloodrave"
