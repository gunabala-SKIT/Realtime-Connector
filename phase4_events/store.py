"""SQLite event store (BRD S16 event model).

One writer (the pipeline) + many readers (the API) - WAL mode makes that safe.
Events are an append-only log: employee name/code are denormalised into each row
so a later rename or delete never rewrites history.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_uid       TEXT UNIQUE NOT NULL,
    ts              REAL NOT NULL,               -- unix epoch, event time
    camera_id       TEXT NOT NULL,
    track_id        INTEGER NOT NULL,
    event_type      TEXT NOT NULL,               -- identity_recognized | identity_unknown | identity_unverified
    identity_status TEXT NOT NULL,               -- RECOGNIZED | UNKNOWN | UNVERIFIED
    employee_code   TEXT,
    employee_name   TEXT,
    confidence      REAL,
    zone            TEXT,                         -- reserved for Phase 6
    snapshot        TEXT,                         -- relative path under snapshot_dir
    alert_status    TEXT NOT NULL DEFAULT 'none', -- none | open | ack | closed (Phase 7)
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_ts       ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_status   ON events(identity_status);
CREATE INDEX IF NOT EXISTS idx_events_camera   ON events(camera_id);
CREATE INDEX IF NOT EXISTS idx_events_alert    ON events(alert_status);

-- read-only mirror of the Chroma gallery, so the API never opens Chroma itself
CREATE TABLE IF NOT EXISTS employees (
    code        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    department  TEXT,
    status      TEXT NOT NULL DEFAULT 'active',
    n_images    INTEGER NOT NULL DEFAULT 0,
    synced_at   REAL NOT NULL
);
"""


@dataclass
class Event:
    id: int | None
    event_uid: str
    ts: float
    camera_id: str
    track_id: int
    event_type: str
    identity_status: str
    employee_code: str | None
    employee_name: str | None
    confidence: float | None
    zone: str | None
    snapshot: str | None
    alert_status: str
    created_at: float

    def to_dict(self) -> dict:
        return asdict(self)


class EventStore:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=4000")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    # ------------------------------------------------------------------ write
    def insert(self, ev: Event) -> Event:
        cur = self.conn.execute(
            "INSERT INTO events (event_uid, ts, camera_id, track_id, event_type, identity_status, "
            "employee_code, employee_name, confidence, zone, snapshot, alert_status, created_at) "
            "VALUES (:event_uid,:ts,:camera_id,:track_id,:event_type,:identity_status,"
            ":employee_code,:employee_name,:confidence,:zone,:snapshot,:alert_status,:created_at)",
            ev.to_dict(),
        )
        self.conn.commit()
        ev.id = int(cur.lastrowid)
        return ev

    def set_alert_status(self, event_id: int, status: str) -> bool:
        cur = self.conn.execute(
            "UPDATE events SET alert_status=? WHERE id=?", (status, event_id)
        )
        self.conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------- read
    def get(self, event_id: int) -> Event | None:
        row = self.conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        return _row_to_event(row) if row else None

    def query(self, *, since: float | None = None, until: float | None = None,
              camera_id: str | None = None, identity_status: str | None = None,
              employee_code: str | None = None, alert_status: str | None = None,
              after_id: int | None = None, limit: int = 100, offset: int = 0,
              order: str = "desc") -> list[Event]:
        where, params = [], []
        if since is not None:
            where.append("ts >= ?"); params.append(since)
        if until is not None:
            where.append("ts <= ?"); params.append(until)
        if camera_id:
            where.append("camera_id = ?"); params.append(camera_id)
        if identity_status:
            where.append("identity_status = ?"); params.append(identity_status)
        if employee_code:
            where.append("employee_code = ?"); params.append(employee_code)
        if alert_status:
            where.append("alert_status = ?"); params.append(alert_status)
        if after_id is not None:
            where.append("id > ?"); params.append(after_id)
        sql = "SELECT * FROM events"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY id {'ASC' if order == 'asc' else 'DESC'} LIMIT ? OFFSET ?"
        params += [max(1, min(limit, 1000)), max(0, offset)]
        return [_row_to_event(r) for r in self.conn.execute(sql, params).fetchall()]

    def max_id(self) -> int:
        row = self.conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM events").fetchone()
        return int(row["m"])

    def stats(self, since: float) -> dict:
        rows = self.conn.execute(
            "SELECT identity_status, COUNT(*) AS n FROM events WHERE ts >= ? GROUP BY identity_status",
            (since,),
        ).fetchall()
        by_status = {r["identity_status"]: r["n"] for r in rows}
        total = self.conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE ts >= ?", (since,)
        ).fetchone()["n"]
        open_alerts = self.conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE alert_status='open'"
        ).fetchone()["n"]
        return {
            "since": since,
            "total": total,
            "recognized": by_status.get("RECOGNIZED", 0),
            "unknown": by_status.get("UNKNOWN", 0),
            "unverified": by_status.get("UNVERIFIED", 0),
            "open_alerts": open_alerts,
        }

    # ------------------------------------------------------------- employees
    def sync_employees(self, employees: list[dict]) -> None:
        now = time.time()
        with self.conn:
            self.conn.execute("DELETE FROM employees")
            self.conn.executemany(
                "INSERT INTO employees (code, name, department, status, n_images, synced_at) "
                "VALUES (:code, :name, :department, :status, :n_images, :synced_at)",
                [{**e, "synced_at": now} for e in employees],
            )

    def list_employees(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT code, name, department, status, n_images FROM employees ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self.conn.close()


def _row_to_event(row: sqlite3.Row) -> Event:
    return Event(**{k: row[k] for k in row.keys()})
