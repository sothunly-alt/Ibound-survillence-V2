from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            event_type TEXT NOT NULL,
            abs_path TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS minutes (
            minute TEXT PRIMARY KEY,
            max_persons INTEGER NOT NULL,
            occupied_frames INTEGER NOT NULL,
            total_frames INTEGER NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def insert_event(
    conn: sqlite3.Connection,
    event_type: str,
    ts: datetime,
    abs_path: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO events (ts, event_type, abs_path) VALUES (?, ?, ?)",
        (ts.isoformat(timespec="seconds"), event_type, abs_path),
    )
    conn.commit()


def has_opened_today(conn: sqlite3.Connection, day: date) -> bool:
    prefix = day.isoformat()
    row = conn.execute(
        "SELECT 1 FROM events WHERE event_type = 'opened' AND ts LIKE ? LIMIT 1",
        (f"{prefix}%",),
    ).fetchone()
    return row is not None


def upsert_minute(
    conn: sqlite3.Connection,
    minute: str,
    person_count: int,
    occupied: bool,
) -> None:
    row = conn.execute("SELECT * FROM minutes WHERE minute = ?", (minute,)).fetchone()
    if row is None:
        conn.execute(
            """
            INSERT INTO minutes (minute, max_persons, occupied_frames, total_frames)
            VALUES (?, ?, ?, 1)
            """,
            (minute, person_count, 1 if occupied else 0),
        )
    else:
        conn.execute(
            """
            UPDATE minutes
            SET max_persons = MAX(max_persons, ?),
                occupied_frames = occupied_frames + ?,
                total_frames = total_frames + 1
            WHERE minute = ?
            """,
            (person_count, 1 if occupied else 0, minute),
        )
    conn.commit()


def day_events(conn: sqlite3.Connection, day: date) -> list[sqlite3.Row]:
    prefix = day.isoformat()
    return list(
        conn.execute(
            "SELECT * FROM events WHERE ts LIKE ? ORDER BY ts ASC",
            (f"{prefix}%",),
        )
    )


def day_minutes(conn: sqlite3.Connection, day: date) -> list[sqlite3.Row]:
    prefix = day.isoformat()
    return list(
        conn.execute(
            "SELECT * FROM minutes WHERE minute LIKE ? ORDER BY minute ASC",
            (f"{prefix}%",),
        )
    )
