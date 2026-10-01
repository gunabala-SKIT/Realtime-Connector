"""The Phase 6 database (spec section 19). SQLite, not the spec's "recommended" PostgreSQL - a
single-process, single-camera MVP (spec section 20: "the MVP should work with one camera first")
has no concurrent-writer problem a file database can't handle, and it needs nothing installed or
running. Swapping to a real server-based database later is a connection-string change, not a
rewrite - every query here goes through this one module.

Only two tables exist: `production_events` (Phase 6's actual deliverable - "convert activities
into Start/End/Duration/Worker/Camera/Zone/Confidence and store them in the database") and
`workers` (spec section 13's explicit MVP allowance: "allow manual worker assignment if automatic
identification is not yet reliable... do not make face recognition mandatory for the first
version"). The much longer entity list in spec section 19 (Camera, CameraZone, ProductionStage,
DetectedObject, ProductionOrder, FabricBatch, FabricPiece, Bundle, Machine) is that section's own
*suggested* full schema for the finished product, not a Phase 6 requirement - building them now,
with no feature yet that reads or writes them, would be speculative. They're added if and when a
later phase actually needs them.

A single shared `sqlite3.Connection` protected by one `threading.Lock`, not a connection-per-call
or a connection pool: this is the exact fix already proven in this repo for the same class of bug
(see `phase5_dashboard`'s `EventStore` - a shared connection used from multiple threads without
synchronization caused intermittent "database is locked" / corrupt-read errors under real
concurrent load, not caught by single-threaded tests). Every public method here acquires the lock
for its full duration, including the commit, so two threads can never interleave statements on the
one connection.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS production_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id TEXT NOT NULL,
    track_id INTEGER NOT NULL,
    worker TEXT NOT NULL,
    stage TEXT NOT NULL,
    zone TEXT,
    start_ts REAL NOT NULL,
    end_ts REAL NOT NULL,
    duration_s REAL NOT NULL,
    confidence REAL NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_stage ON production_events(stage);
CREATE INDEX IF NOT EXISTS idx_events_start ON production_events(start_ts);
CREATE INDEX IF NOT EXISTS idx_events_worker ON production_events(worker);

CREATE TABLE IF NOT EXISTS workers (
    track_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    assigned_at REAL NOT NULL
);
"""


class Database:
    """Opens one connection for the whole process and serializes every access to it through
    `self._lock`. `check_same_thread=False` is safe only because of that lock - without it this
    would be the exact bug `phase5_dashboard` hit."""

    def __init__(self, path: Path | str) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """A single write statement, committed immediately. Returns the cursor (its
        `lastrowid` is valid for the caller to read before the lock is released)."""
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
