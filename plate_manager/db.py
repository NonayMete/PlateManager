"""SQLite schema and connection handling.

Rows carry a `uid` (uuid4) alongside the local integer id.  The uid is what
makes merging an exported bundle from another computer possible: the same well
keeps its uid everywhere, so an import can tell "same well, newer edit" from
"a well I have never seen".
"""
from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS patients (
    id         INTEGER PRIMARY KEY,
    uid        TEXT NOT NULL UNIQUE,
    code       TEXT NOT NULL UNIQUE,       -- de-identified patient/donor ID
    diagnosis  TEXT DEFAULT '',
    notes      TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cell_lines (
    id         INTEGER PRIMARY KEY,
    uid        TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL UNIQUE,
    patient_id INTEGER REFERENCES patients(id) ON DELETE SET NULL,
    species    TEXT DEFAULT '',
    tissue     TEXT DEFAULT '',
    notes      TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiments (
    id         INTEGER PRIMARY KEY,
    uid        TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL UNIQUE,
    owner      TEXT DEFAULT '',
    colour     TEXT DEFAULT '',
    notes      TEXT DEFAULT '',
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plates (
    id         INTEGER PRIMARY KEY,
    uid        TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,
    format     TEXT NOT NULL DEFAULT '96-well',
    n_rows     INTEGER NOT NULL,
    n_cols     INTEGER NOT NULL,
    location   TEXT DEFAULT '',            -- incubator / shelf / freezer box
    owner      TEXT DEFAULT '',
    notes      TEXT DEFAULT '',
    archived   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wells (
    id            INTEGER PRIMARY KEY,
    uid           TEXT NOT NULL UNIQUE,
    plate_id      INTEGER NOT NULL REFERENCES plates(id) ON DELETE CASCADE,
    row_idx       INTEGER NOT NULL,
    col_idx       INTEGER NOT NULL,
    cell_line_id  INTEGER REFERENCES cell_lines(id) ON DELETE SET NULL,
    patient_id    INTEGER REFERENCES patients(id) ON DELETE SET NULL,
    experiment_id INTEGER REFERENCES experiments(id) ON DELETE SET NULL,
    status        TEXT DEFAULT '',
    passage       TEXT DEFAULT '',
    treatment     TEXT DEFAULT '',
    medium        TEXT DEFAULT '',
    seeded_on     TEXT DEFAULT '',
    confluence    INTEGER,
    due_date      TEXT DEFAULT '',
    due_task      TEXT DEFAULT '',
    notes         TEXT DEFAULT '',
    updated_at    TEXT NOT NULL,
    UNIQUE (plate_id, row_idx, col_idx)
);

CREATE TABLE IF NOT EXISTS photos (
    id         INTEGER PRIMARY KEY,
    uid        TEXT NOT NULL UNIQUE,
    well_id    INTEGER NOT NULL REFERENCES wells(id) ON DELETE CASCADE,
    rel_path   TEXT NOT NULL,              -- relative to <data_dir>/photos
    taken_at   TEXT DEFAULT '',            -- ISO date or datetime; '' = undated
    caption    TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id       INTEGER PRIMARY KEY,
    uid      TEXT NOT NULL UNIQUE,
    well_id  INTEGER REFERENCES wells(id) ON DELETE CASCADE,
    plate_id INTEGER REFERENCES plates(id) ON DELETE CASCADE,
    ts       TEXT NOT NULL,
    kind     TEXT NOT NULL,
    detail   TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_wells_plate  ON wells(plate_id);
CREATE INDEX IF NOT EXISTS idx_wells_due    ON wells(due_date);
CREATE INDEX IF NOT EXISTS idx_photos_well  ON photos(well_id, taken_at);
CREATE INDEX IF NOT EXISTS idx_events_well  ON events(well_id, ts);
"""


def new_uid() -> str:
    return uuid.uuid4().hex


def connect(path: str | Path) -> sqlite3.Connection:
    """Open (creating if needed) a Plate Manager database."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    init(conn)
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    version = get_meta(conn, "schema_version")
    if version is None:
        set_meta(conn, "schema_version", str(SCHEMA_VERSION))
    else:
        migrate(conn, int(version))
    conn.commit()


def migrate(conn: sqlite3.Connection, from_version: int) -> None:
    """Upgrade an older database in place.

    Only version 1 exists so far; later versions append their steps here and
    bump SCHEMA_VERSION.  Newer-than-known files are left alone (the caller
    warns the user) rather than being downgraded.
    """
    version = from_version
    # (no migrations yet)
    if version != from_version:
        set_meta(conn, "schema_version", str(version))
        conn.commit()


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
