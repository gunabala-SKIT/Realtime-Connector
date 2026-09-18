"""SQLite event store (BRD S16 event model).

One writer (the pipeline) + many readers (the API) - WAL mode makes that safe.
Events are an append-only log: employee name/code are denormalised into each row
so a later rename or delete never rewrites history.
"""
from __future__ import annotations

import json
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

-- Phase 6: user-configurable rules (BRD FR-018/FR-020, S15)
CREATE TABLE IF NOT EXISTS rules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 1,
    identity_status TEXT,                       -- NULL = any; else RECOGNIZED|UNKNOWN|UNVERIFIED
    camera_id       TEXT,                       -- NULL = any
    time_start      TEXT,                       -- "HH:MM" or NULL = any time
    time_end        TEXT,                       -- "HH:MM"; if < time_start, window wraps midnight
    days_of_week    TEXT,                       -- "0,1,2,..6" (Mon=0) or NULL = every day
    priority        TEXT NOT NULL DEFAULT 'high', -- low | medium | high | critical
    notify_email    INTEGER NOT NULL DEFAULT 0,
    cooldown_s      REAL NOT NULL DEFAULT 120,  -- suppress repeat alerts for the same rule+track
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);

-- Phase 6: alerts raised by rule matches (BRD S20 Alert entity, FR-014)
CREATE TABLE IF NOT EXISTS alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_uid       TEXT UNIQUE NOT NULL,
    event_id        INTEGER NOT NULL,
    rule_id         INTEGER,
    rule_name       TEXT,
    camera_id       TEXT NOT NULL,
    identity_status TEXT NOT NULL,
    employee_name   TEXT,
    priority        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'open', -- open | ack | closed
    assigned_to     TEXT,
    comment         TEXT,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status);
CREATE INDEX IF NOT EXISTS idx_alerts_event  ON alerts(event_id);

-- Phase 7: zones (BRD FR-019). Points are normalised [0,1] fractions of the
-- frame, so a zone stays valid regardless of the stream's actual resolution.
CREATE TABLE IF NOT EXISTS zones (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id   TEXT NOT NULL,
    name        TEXT NOT NULL,
    points      TEXT NOT NULL,               -- JSON [[x,y], ...], >= 3 points
    color       TEXT NOT NULL DEFAULT '#3ccfd7',
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_zones_camera ON zones(camera_id);

-- Phase 9: queue / crowd density monitoring. A zone becomes a "queue zone"
-- simply by having `capacity` set (see _ensure_column below for older DBs) -
-- no separate region to draw, since it's the same "people standing
-- somewhere" concept a zone already is, just counted continuously instead
-- of tagged per-event.
CREATE TABLE IF NOT EXISTS queue_checks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    zone_id     INTEGER NOT NULL,
    camera_id   TEXT NOT NULL,
    ts          REAL NOT NULL,
    count       INTEGER NOT NULL,
    capacity    INTEGER NOT NULL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_queue_checks_zone ON queue_checks(zone_id, id);

CREATE TABLE IF NOT EXISTS queue_alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_uid       TEXT UNIQUE NOT NULL,
    queue_check_id  INTEGER NOT NULL,
    zone_id         INTEGER NOT NULL,
    zone_name       TEXT NOT NULL,
    camera_id       TEXT NOT NULL,
    count           INTEGER NOT NULL,
    capacity        INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'open',  -- open | ack | closed
    assigned_to     TEXT,
    comment         TEXT,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_queue_alerts_status ON queue_alerts(status);

-- Phase 8: shelf / inventory monitoring (BRD S8 Retail: shelf monitoring).
-- A shelf is a counting region (same normalised-points idea as a zone) with
-- an expected full count and a restock threshold - independent of `zones`,
-- which is about *people* standing somewhere, not *objects* stocked somewhere.
CREATE TABLE IF NOT EXISTS shelves (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id           TEXT NOT NULL,
    name                TEXT NOT NULL,             -- e.g. "Aisle 3 - Coke 500ml"
    points              TEXT NOT NULL,             -- JSON [[x,y], ...] normalised 0..1
    product_name        TEXT,
    capacity            INTEGER NOT NULL,          -- expected count when fully stocked
    low_stock_threshold REAL NOT NULL DEFAULT 0.7, -- alert when detected/capacity <= this
    notify_email        INTEGER NOT NULL DEFAULT 0,
    cooldown_s          REAL NOT NULL DEFAULT 1800,
    enabled             INTEGER NOT NULL DEFAULT 1,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shelves_camera ON shelves(camera_id);

CREATE TABLE IF NOT EXISTS shelf_checks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    shelf_id        INTEGER NOT NULL,
    camera_id       TEXT NOT NULL,
    ts              REAL NOT NULL,
    detected_count  INTEGER NOT NULL,
    capacity        INTEGER NOT NULL,
    pct_full        REAL NOT NULL,
    snapshot        TEXT,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shelf_checks_shelf ON shelf_checks(shelf_id, id);

CREATE TABLE IF NOT EXISTS shelf_alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_uid       TEXT UNIQUE NOT NULL,
    shelf_check_id  INTEGER NOT NULL,
    shelf_id        INTEGER NOT NULL,
    shelf_name      TEXT NOT NULL,
    camera_id       TEXT NOT NULL,
    detected_count  INTEGER NOT NULL,
    capacity        INTEGER NOT NULL,
    pct_full        REAL NOT NULL,
    status          TEXT NOT NULL DEFAULT 'open',  -- open | ack | closed
    assigned_to     TEXT,
    comment         TEXT,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shelf_alerts_status ON shelf_alerts(status);
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


@dataclass
class Rule:
    id: int | None
    name: str
    enabled: bool
    identity_status: str | None
    camera_id: str | None
    time_start: str | None
    time_end: str | None
    days_of_week: str | None
    zone: str | None
    priority: str
    notify_email: bool
    cooldown_s: float
    created_at: float
    updated_at: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Zone:
    id: int | None
    camera_id: str
    name: str
    points: list          # [[x, y], ...] normalised 0..1
    color: str
    enabled: bool
    created_at: float
    updated_at: float
    capacity: int | None = None        # set -> this zone is also a queue/crowd zone
    queue_cooldown_s: float = 60.0     # min seconds between over-capacity alerts

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class QueueCheck:
    id: int | None
    zone_id: int
    camera_id: str
    ts: float
    count: int
    capacity: int
    created_at: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class QueueAlert:
    id: int | None
    alert_uid: str
    queue_check_id: int
    zone_id: int
    zone_name: str
    camera_id: str
    count: int
    capacity: int
    status: str
    assigned_to: str | None
    comment: str | None
    created_at: float
    updated_at: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Shelf:
    id: int | None
    camera_id: str
    name: str
    points: list           # [[x, y], ...] normalised 0..1
    product_name: str | None
    capacity: int
    low_stock_threshold: float
    notify_email: bool
    cooldown_s: float
    enabled: bool
    created_at: float
    updated_at: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ShelfCheck:
    id: int | None
    shelf_id: int
    camera_id: str
    ts: float
    detected_count: int
    capacity: int
    pct_full: float
    snapshot: str | None
    created_at: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ShelfAlert:
    id: int | None
    alert_uid: str
    shelf_check_id: int
    shelf_id: int
    shelf_name: str
    camera_id: str
    detected_count: int
    capacity: int
    pct_full: float
    status: str
    assigned_to: str | None
    comment: str | None
    created_at: float
    updated_at: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Alert:
    id: int | None
    alert_uid: str
    event_id: int
    rule_id: int | None
    rule_name: str | None
    camera_id: str
    identity_status: str
    employee_name: str | None
    priority: str
    status: str
    assigned_to: str | None
    comment: str | None
    created_at: float
    updated_at: float

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
        self._ensure_column("rules", "zone", "TEXT")  # added in Phase 7, migrate older DBs
        self._ensure_column("zones", "capacity", "INTEGER")             # Phase 9
        self._ensure_column("zones", "queue_cooldown_s", "REAL DEFAULT 60")  # Phase 9

    def _ensure_column(self, table: str, col: str, coltype: str) -> None:
        cols = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
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
        """Set an event's quick-glance alert flag, and keep any linked alerts row(s)
        (created by the rule engine) in sync with it."""
        with self.conn:
            cur = self.conn.execute(
                "UPDATE events SET alert_status=? WHERE id=?", (status, event_id)
            )
            if status in ("ack", "closed"):
                self.conn.execute(
                    "UPDATE alerts SET status=?, updated_at=? WHERE event_id=? AND status != 'closed'",
                    (status, time.time(), event_id),
                )
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
            "SELECT COUNT(*) AS n FROM alerts WHERE status='open'"
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

    # ------------------------------------------------------------------ zones
    def add_zone(self, *, camera_id: str, name: str, points: list,
                 color: str = "#3ccfd7", enabled: bool = True,
                 capacity: int | None = None, queue_cooldown_s: float = 60.0) -> Zone:
        now = time.time()
        cur = self.conn.execute(
            "INSERT INTO zones (camera_id, name, points, color, enabled, created_at, updated_at, "
            "capacity, queue_cooldown_s) VALUES (?,?,?,?,?,?,?,?,?)",
            (camera_id, name, json.dumps(points), color, int(enabled), now, now,
             capacity, queue_cooldown_s),
        )
        self.conn.commit()
        return self.get_zone(int(cur.lastrowid))

    def get_zone(self, zone_id: int) -> Zone | None:
        row = self.conn.execute("SELECT * FROM zones WHERE id=?", (zone_id,)).fetchone()
        return _row_to_zone(row) if row else None

    def list_zones(self, *, camera_id: str | None = None, enabled_only: bool = False) -> list[Zone]:
        where, params = [], []
        if camera_id:
            where.append("camera_id = ?"); params.append(camera_id)
        if enabled_only:
            where.append("enabled = 1")
        sql = "SELECT * FROM zones"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id"
        return [_row_to_zone(r) for r in self.conn.execute(sql, params).fetchall()]

    def update_zone(self, zone_id: int, **fields) -> Zone | None:
        if not fields:
            return self.get_zone(zone_id)
        fields = dict(fields)
        if "points" in fields:
            fields["points"] = json.dumps(fields["points"])
        if "enabled" in fields:
            fields["enabled"] = int(fields["enabled"])
        cols = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE zones SET {cols}, updated_at=? WHERE id=?",
                          [*fields.values(), time.time(), zone_id])
        self.conn.commit()
        return self.get_zone(zone_id)

    def delete_zone(self, zone_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM zones WHERE id=?", (zone_id,))
        self.conn.commit()
        return cur.rowcount > 0

    # ----------------------------------------------------------- queue checks
    def add_queue_check(self, *, zone_id: int, camera_id: str, ts: float,
                         count: int, capacity: int) -> QueueCheck:
        cur = self.conn.execute(
            "INSERT INTO queue_checks (zone_id, camera_id, ts, count, capacity, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (zone_id, camera_id, ts, count, capacity, time.time()),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM queue_checks WHERE id=?", (cur.lastrowid,)).fetchone()
        return _row_to_queue_check(row)

    def list_queue_checks(self, *, zone_id: int | None = None, limit: int = 100) -> list[QueueCheck]:
        sql = "SELECT * FROM queue_checks"
        params: list = []
        if zone_id is not None:
            sql += " WHERE zone_id = ?"; params.append(zone_id)
        sql += " ORDER BY id DESC LIMIT ?"; params.append(max(1, min(limit, 1000)))
        return [_row_to_queue_check(r) for r in self.conn.execute(sql, params).fetchall()]

    def latest_queue_checks(self) -> dict[int, QueueCheck]:
        """Most recent check per zone_id, for a dashboard summary."""
        rows = self.conn.execute(
            "SELECT * FROM queue_checks WHERE id IN "
            "(SELECT MAX(id) FROM queue_checks GROUP BY zone_id)"
        ).fetchall()
        return {r["zone_id"]: _row_to_queue_check(r) for r in rows}

    # ----------------------------------------------------------- queue alerts
    def create_queue_alert(self, check: QueueCheck, zone: Zone) -> QueueAlert:
        import uuid
        now = time.time()
        cur = self.conn.execute(
            "INSERT INTO queue_alerts (alert_uid, queue_check_id, zone_id, zone_name, camera_id, "
            "count, capacity, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, check.id, zone.id, zone.name, zone.camera_id,
             check.count, check.capacity, "open", now, now),
        )
        self.conn.commit()
        return self.get_queue_alert(int(cur.lastrowid))

    def get_queue_alert(self, alert_id: int) -> QueueAlert | None:
        row = self.conn.execute("SELECT * FROM queue_alerts WHERE id=?", (alert_id,)).fetchone()
        return _row_to_queue_alert(row) if row else None

    def list_queue_alerts(self, *, status: str | None = None, limit: int = 100) -> list[QueueAlert]:
        sql = "SELECT * FROM queue_alerts"
        params: list = []
        if status:
            sql += " WHERE status = ?"; params.append(status)
        sql += " ORDER BY id DESC LIMIT ?"; params.append(max(1, min(limit, 1000)))
        return [_row_to_queue_alert(r) for r in self.conn.execute(sql, params).fetchall()]

    def update_queue_alert(self, alert_id: int, **fields) -> QueueAlert | None:
        if not fields:
            return self.get_queue_alert(alert_id)
        cols = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE queue_alerts SET {cols}, updated_at=? WHERE id=?",
                          [*fields.values(), time.time(), alert_id])
        self.conn.commit()
        return self.get_queue_alert(alert_id)

    # ----------------------------------------------------------------- shelves
    def add_shelf(self, *, camera_id: str, name: str, points: list, capacity: int,
                  product_name: str | None = None, low_stock_threshold: float = 0.7,
                  notify_email: bool = False, cooldown_s: float = 1800,
                  enabled: bool = True) -> Shelf:
        now = time.time()
        cur = self.conn.execute(
            "INSERT INTO shelves (camera_id, name, points, product_name, capacity, "
            "low_stock_threshold, notify_email, cooldown_s, enabled, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (camera_id, name, json.dumps(points), product_name, capacity, low_stock_threshold,
             int(notify_email), cooldown_s, int(enabled), now, now),
        )
        self.conn.commit()
        return self.get_shelf(int(cur.lastrowid))

    def get_shelf(self, shelf_id: int) -> Shelf | None:
        row = self.conn.execute("SELECT * FROM shelves WHERE id=?", (shelf_id,)).fetchone()
        return _row_to_shelf(row) if row else None

    def list_shelves(self, *, camera_id: str | None = None, enabled_only: bool = False) -> list[Shelf]:
        where, params = [], []
        if camera_id:
            where.append("camera_id = ?"); params.append(camera_id)
        if enabled_only:
            where.append("enabled = 1")
        sql = "SELECT * FROM shelves"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id"
        return [_row_to_shelf(r) for r in self.conn.execute(sql, params).fetchall()]

    def update_shelf(self, shelf_id: int, **fields) -> Shelf | None:
        if not fields:
            return self.get_shelf(shelf_id)
        fields = dict(fields)
        if "points" in fields:
            fields["points"] = json.dumps(fields["points"])
        if "enabled" in fields:
            fields["enabled"] = int(fields["enabled"])
        if "notify_email" in fields:
            fields["notify_email"] = int(fields["notify_email"])
        cols = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE shelves SET {cols}, updated_at=? WHERE id=?",
                          [*fields.values(), time.time(), shelf_id])
        self.conn.commit()
        return self.get_shelf(shelf_id)

    def delete_shelf(self, shelf_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM shelves WHERE id=?", (shelf_id,))
        self.conn.commit()
        return cur.rowcount > 0

    # ----------------------------------------------------------- shelf checks
    def add_shelf_check(self, *, shelf_id: int, camera_id: str, ts: float, detected_count: int,
                        capacity: int, pct_full: float, snapshot: str | None) -> ShelfCheck:
        cur = self.conn.execute(
            "INSERT INTO shelf_checks (shelf_id, camera_id, ts, detected_count, capacity, "
            "pct_full, snapshot, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (shelf_id, camera_id, ts, detected_count, capacity, pct_full, snapshot, time.time()),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM shelf_checks WHERE id=?", (cur.lastrowid,)).fetchone()
        return _row_to_shelf_check(row)

    def list_shelf_checks(self, *, shelf_id: int | None = None, limit: int = 100) -> list[ShelfCheck]:
        sql = "SELECT * FROM shelf_checks"
        params: list = []
        if shelf_id is not None:
            sql += " WHERE shelf_id = ?"; params.append(shelf_id)
        sql += " ORDER BY id DESC LIMIT ?"; params.append(max(1, min(limit, 1000)))
        return [_row_to_shelf_check(r) for r in self.conn.execute(sql, params).fetchall()]

    def latest_shelf_checks(self) -> dict[int, ShelfCheck]:
        """Most recent check per shelf_id, for a dashboard summary."""
        rows = self.conn.execute(
            "SELECT * FROM shelf_checks WHERE id IN "
            "(SELECT MAX(id) FROM shelf_checks GROUP BY shelf_id)"
        ).fetchall()
        return {r["shelf_id"]: _row_to_shelf_check(r) for r in rows}

    # ----------------------------------------------------------- shelf alerts
    def create_shelf_alert(self, check: ShelfCheck, shelf: Shelf) -> ShelfAlert:
        import uuid
        now = time.time()
        cur = self.conn.execute(
            "INSERT INTO shelf_alerts (alert_uid, shelf_check_id, shelf_id, shelf_name, camera_id, "
            "detected_count, capacity, pct_full, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, check.id, shelf.id, shelf.name, shelf.camera_id,
             check.detected_count, check.capacity, check.pct_full, "open", now, now),
        )
        self.conn.commit()
        return self.get_shelf_alert(int(cur.lastrowid))

    def get_shelf_alert(self, alert_id: int) -> ShelfAlert | None:
        row = self.conn.execute("SELECT * FROM shelf_alerts WHERE id=?", (alert_id,)).fetchone()
        return _row_to_shelf_alert(row) if row else None

    def list_shelf_alerts(self, *, status: str | None = None, limit: int = 100) -> list[ShelfAlert]:
        sql = "SELECT * FROM shelf_alerts"
        params: list = []
        if status:
            sql += " WHERE status = ?"; params.append(status)
        sql += " ORDER BY id DESC LIMIT ?"; params.append(max(1, min(limit, 1000)))
        return [_row_to_shelf_alert(r) for r in self.conn.execute(sql, params).fetchall()]

    def update_shelf_alert(self, alert_id: int, **fields) -> ShelfAlert | None:
        if not fields:
            return self.get_shelf_alert(alert_id)
        cols = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE shelf_alerts SET {cols}, updated_at=? WHERE id=?",
                          [*fields.values(), time.time(), alert_id])
        self.conn.commit()
        return self.get_shelf_alert(alert_id)

    # ----------------------------------------------------------------- rules
    def add_rule(self, *, name: str, identity_status: str | None, camera_id: str | None,
                 time_start: str | None, time_end: str | None, days_of_week: str | None,
                 priority: str, notify_email: bool, cooldown_s: float,
                 zone: str | None = None, enabled: bool = True) -> Rule:
        now = time.time()
        cur = self.conn.execute(
            "INSERT INTO rules (name, enabled, identity_status, camera_id, time_start, time_end, "
            "days_of_week, zone, priority, notify_email, cooldown_s, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (name, int(enabled), identity_status, camera_id, time_start, time_end,
             days_of_week, zone, priority, int(notify_email), cooldown_s, now, now),
        )
        self.conn.commit()
        return self.get_rule(int(cur.lastrowid))

    def get_rule(self, rule_id: int) -> Rule | None:
        row = self.conn.execute("SELECT * FROM rules WHERE id=?", (rule_id,)).fetchone()
        return _row_to_rule(row) if row else None

    def list_rules(self, *, enabled_only: bool = False) -> list[Rule]:
        sql = "SELECT * FROM rules"
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY id"
        return [_row_to_rule(r) for r in self.conn.execute(sql).fetchall()]

    def update_rule(self, rule_id: int, **fields) -> Rule | None:
        if not fields:
            return self.get_rule(rule_id)
        cols = ", ".join(f"{k}=?" for k in fields)
        vals = list(fields.values())
        if "enabled" in fields:
            i = list(fields.keys()).index("enabled")
            vals[i] = int(vals[i])
        if "notify_email" in fields:
            i = list(fields.keys()).index("notify_email")
            vals[i] = int(vals[i])
        self.conn.execute(f"UPDATE rules SET {cols}, updated_at=? WHERE id=?",
                          [*vals, time.time(), rule_id])
        self.conn.commit()
        return self.get_rule(rule_id)

    def delete_rule(self, rule_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM rules WHERE id=?", (rule_id,))
        self.conn.commit()
        return cur.rowcount > 0

    # ---------------------------------------------------------------- alerts
    def create_alert(self, event: Event, rule: Rule) -> Alert:
        import uuid
        now = time.time()
        cur = self.conn.execute(
            "INSERT INTO alerts (alert_uid, event_id, rule_id, rule_name, camera_id, "
            "identity_status, employee_name, priority, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, event.id, rule.id, rule.name, event.camera_id,
             event.identity_status, event.employee_name, rule.priority, "open", now, now),
        )
        with self.conn:
            self.conn.execute("UPDATE events SET alert_status='open' WHERE id=?", (event.id,))
        self.conn.commit()
        return self.get_alert(int(cur.lastrowid))

    def get_alert(self, alert_id: int) -> Alert | None:
        row = self.conn.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
        return _row_to_alert(row) if row else None

    def list_alerts(self, *, status: str | None = None, priority: str | None = None,
                     limit: int = 100, offset: int = 0) -> list[Alert]:
        where, params = [], []
        if status:
            where.append("status = ?"); params.append(status)
        if priority:
            where.append("priority = ?"); params.append(priority)
        sql = "SELECT * FROM alerts"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params += [max(1, min(limit, 1000)), max(0, offset)]
        return [_row_to_alert(r) for r in self.conn.execute(sql, params).fetchall()]

    def update_alert(self, alert_id: int, **fields) -> Alert | None:
        if not fields:
            return self.get_alert(alert_id)
        alert = self.get_alert(alert_id)
        if not alert:
            return None
        cols = ", ".join(f"{k}=?" for k in fields)
        with self.conn:
            self.conn.execute(f"UPDATE alerts SET {cols}, updated_at=? WHERE id=?",
                              [*fields.values(), time.time(), alert_id])
            if "status" in fields:
                self.conn.execute("UPDATE events SET alert_status=? WHERE id=?",
                                  (fields["status"], alert.event_id))
        return self.get_alert(alert_id)

    def get_alerts_for_events(self, event_ids: list[int]) -> dict[int, Alert]:
        """Latest alert per event id, for enriching an /events listing."""
        if not event_ids:
            return {}
        qmarks = ",".join("?" * len(event_ids))
        rows = self.conn.execute(
            f"SELECT * FROM alerts WHERE event_id IN ({qmarks}) ORDER BY id DESC", event_ids
        ).fetchall()
        out: dict[int, Alert] = {}
        for r in rows:
            a = _row_to_alert(r)
            out.setdefault(a.event_id, a)  # first hit per event_id = most recent (DESC)
        return out

    def close(self) -> None:
        self.conn.close()


def _row_to_event(row: sqlite3.Row) -> Event:
    return Event(**{k: row[k] for k in row.keys()})


def _row_to_rule(row: sqlite3.Row) -> Rule:
    d = {k: row[k] for k in row.keys()}
    d["enabled"] = bool(d["enabled"])
    d["notify_email"] = bool(d["notify_email"])
    return Rule(**d)


def _row_to_alert(row: sqlite3.Row) -> Alert:
    return Alert(**{k: row[k] for k in row.keys()})


def _row_to_zone(row: sqlite3.Row) -> Zone:
    d = {k: row[k] for k in row.keys()}
    d["points"] = json.loads(d["points"])
    d["enabled"] = bool(d["enabled"])
    return Zone(**d)


def _row_to_queue_check(row: sqlite3.Row) -> QueueCheck:
    return QueueCheck(**{k: row[k] for k in row.keys()})


def _row_to_queue_alert(row: sqlite3.Row) -> QueueAlert:
    return QueueAlert(**{k: row[k] for k in row.keys()})


def _row_to_shelf(row: sqlite3.Row) -> Shelf:
    d = {k: row[k] for k in row.keys()}
    d["points"] = json.loads(d["points"])
    d["enabled"] = bool(d["enabled"])
    d["notify_email"] = bool(d["notify_email"])
    return Shelf(**d)


def _row_to_shelf_check(row: sqlite3.Row) -> ShelfCheck:
    return ShelfCheck(**{k: row[k] for k in row.keys()})


def _row_to_shelf_alert(row: sqlite3.Row) -> ShelfAlert:
    return ShelfAlert(**{k: row[k] for k in row.keys()})
