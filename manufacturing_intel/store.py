"""SQLite persistence for the Manufacturing Process Intelligence Platform.

One shared connection, every access serialised by an RLock (FastAPI runs handlers on a
thread pool and the camera/alert threads write too - sharing a sqlite3 connection
between threads without a lock corrupts state). WAL mode lets a second *process*
(the demo feeder, another camera box) write to the same file.

Rows come back as plain dicts. Times are epoch seconds (float, UTC); anything that
needs a calendar (days, shifts) converts to local time in `periods.py`.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS factories(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, shifts TEXT NOT NULL DEFAULT '[]', created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS lines(
  id INTEGER PRIMARY KEY, factory_id INTEGER NOT NULL REFERENCES factories(id),
  name TEXT NOT NULL, UNIQUE(factory_id, name));
CREATE TABLE IF NOT EXISTS products(
  id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL, target_cycle_min REAL);
CREATE TABLE IF NOT EXISTS cameras(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, source TEXT NOT NULL,
  line_id INTEGER REFERENCES lines(id), detector TEXT NOT NULL DEFAULT 'motion',
  params TEXT NOT NULL DEFAULT '{}', decode_qr INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS stations(
  id INTEGER PRIMARY KEY, line_id INTEGER NOT NULL REFERENCES lines(id), name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('source','process','sink')),
  camera_id INTEGER REFERENCES cameras(id), zone TEXT,
  expected_process_min REAL, expected_wait_qty INTEGER NOT NULL DEFAULT 10,
  target_units_per_h REAL, reject_threshold_pct REAL NOT NULL DEFAULT 5,
  idle_alert_min REAL NOT NULL DEFAULT 25, capacity INTEGER NOT NULL DEFAULT 1, UNIQUE(line_id, name));
CREATE TABLE IF NOT EXISTS routes(
  id INTEGER PRIMARY KEY, line_id INTEGER NOT NULL REFERENCES lines(id),
  product_id INTEGER NOT NULL REFERENCES products(id), name TEXT NOT NULL, UNIQUE(line_id, product_id));
CREATE TABLE IF NOT EXISTS route_steps(
  id INTEGER PRIMARY KEY, route_id INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL, station_id INTEGER NOT NULL REFERENCES stations(id),
  expected_process_min REAL, UNIQUE(route_id, seq));
CREATE TABLE IF NOT EXISTS batches(
  id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE, product_id INTEGER NOT NULL REFERENCES products(id),
  line_id INTEGER NOT NULL REFERENCES lines(id), route_id INTEGER NOT NULL REFERENCES routes(id),
  qty_planned INTEGER, status TEXT NOT NULL DEFAULT 'active', created_at REAL NOT NULL, closed_at REAL);
CREATE TABLE IF NOT EXISTS units(
  id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE, batch_id INTEGER NOT NULL REFERENCES batches(id),
  product_id INTEGER NOT NULL, line_id INTEGER NOT NULL, route_id INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'in_progress' CHECK(status IN ('in_progress','finished','rejected')),
  step_seq INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL, finished_at REAL, rejected_at REAL,
  cycle_s REAL, source TEXT NOT NULL DEFAULT 'api');
CREATE TABLE IF NOT EXISTS visits(
  id INTEGER PRIMARY KEY, unit_id INTEGER NOT NULL REFERENCES units(id), station_id INTEGER NOT NULL REFERENCES stations(id),
  step_seq INTEGER NOT NULL, arrived_ts REAL, start_ts REAL, end_ts REAL,
  outcome TEXT NOT NULL CHECK(outcome IN ('waiting','processing','completed','rejected','skipped')),
  reject_reason TEXT, processing_s REAL, waiting_s REAL, UNIQUE(unit_id, step_seq));
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, received_at REAL NOT NULL, type TEXT NOT NULL,
  station_id INTEGER, unit_id INTEGER, unit_code TEXT, source TEXT NOT NULL, camera_id INTEGER,
  result TEXT NOT NULL, detail TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY, key TEXT NOT NULL, kind TEXT NOT NULL, station_id INTEGER, line_id INTEGER,
  severity TEXT NOT NULL, message TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'open', opened_at REAL NOT NULL, updated_at REAL NOT NULL,
  ack_at REAL, resolved_at REAL);
CREATE INDEX IF NOT EXISTS ix_visits_station_arr ON visits(station_id, arrived_ts);
CREATE INDEX IF NOT EXISTS ix_visits_station_end ON visits(station_id, end_ts);
CREATE INDEX IF NOT EXISTS ix_visits_unit ON visits(unit_id);
CREATE INDEX IF NOT EXISTS ix_units_line_created ON units(line_id, created_at);
CREATE INDEX IF NOT EXISTS ix_units_batch ON units(batch_id);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS ix_events_station_ts ON events(station_id, ts);
CREATE INDEX IF NOT EXISTS ix_alerts_status ON alerts(status);
"""

DEFAULT_SHIFTS = [
    {"name": "Morning", "start": "06:00", "end": "14:00"},
    {"name": "Afternoon", "start": "14:00", "end": "22:00"},
    {"name": "Night", "start": "22:00", "end": "06:00"},
]
REJECT_REASONS = ("dimension_issue", "surface_defect", "machine_error", "material_defect",
                  "operator_error", "quality_failure", "other")
STATION_KINDS = ("source", "process", "sink")


class StoreError(ValueError):
    """Bad input to the store (validation) - the API turns these into 4xx."""


def _row(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r is not None else None


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._depth = 0
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")   # safe with WAL; a power cut can lose the last commits, not corrupt
        self.conn.execute("PRAGMA busy_timeout=8000")
        with self._lock, self.conn:
            self.conn.executescript(SCHEMA)
            cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(stations)")}
            if "capacity" not in cols:  # database created before `capacity` existed
                self.conn.execute("ALTER TABLE stations ADD COLUMN capacity INTEGER NOT NULL DEFAULT 1")

    # ------------------------------------------------------------ primitives
    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Serialised transaction: commit on success, roll back on any exception. Re-entrant: a
        `tx()` inside another one joins it (one commit at the end - bulk loads rely on this)."""
        with self._lock:
            if self._depth:
                self._depth += 1
                try:
                    yield self.conn
                finally:
                    self._depth -= 1
                return
            self._depth = 1
            try:
                # Take the write lock up front. A transaction that starts by reading and later tries to
                # write fails at once (SQLITE_BUSY_SNAPSHOT, "database is locked") when another process
                # has committed in between, and the busy timeout can't help; BEGIN IMMEDIATE waits instead.
                self.conn.execute("BEGIN IMMEDIATE")
                try:
                    yield self.conn
                except BaseException:
                    self.conn.rollback()
                    raise
                else:
                    self.conn.commit()
            finally:
                self._depth = 0

    def q(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def q1(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        with self._lock:
            return _row(self.conn.execute(sql, tuple(params)).fetchone())

    def scalar(self, sql: str, params: Iterable[Any] = (), default: Any = None) -> Any:
        with self._lock:
            r = self.conn.execute(sql, tuple(params)).fetchone()
            return default if r is None or r[0] is None else r[0]

    def x(self, sql: str, params: Iterable[Any] = ()) -> int:
        """Execute one write statement in its own transaction; returns lastrowid."""
        with self.tx() as c:
            return c.execute(sql, tuple(params)).lastrowid

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    # ---------------------------------------------------------------- factory
    def ensure_factory(self, name: str = "My Factory") -> dict:
        f = self.q1("SELECT * FROM factories ORDER BY id LIMIT 1")
        if f:
            return self._factory(f)
        fid = self.x("INSERT INTO factories(name, shifts, created_at) VALUES(?,?,?)",
                     (name, json.dumps(DEFAULT_SHIFTS), time.time()))
        return self._factory(self.q1("SELECT * FROM factories WHERE id=?", (fid,)))

    @staticmethod
    def _factory(row: dict) -> dict:
        row = dict(row)
        row["shifts"] = json.loads(row["shifts"] or "[]")
        return row

    def update_factory(self, name: str | None = None, shifts: list[dict] | None = None) -> dict:
        f = self.ensure_factory()
        if shifts is not None:
            validate_shifts(shifts)
        with self.tx() as c:
            if name:
                c.execute("UPDATE factories SET name=? WHERE id=?", (name.strip(), f["id"]))
            if shifts is not None:
                c.execute("UPDATE factories SET shifts=? WHERE id=?", (json.dumps(shifts), f["id"]))
        return self.ensure_factory()

    # ------------------------------------------------------------------ lines
    def list_lines(self) -> list[dict]:
        return self.q("SELECT * FROM lines ORDER BY id")

    def get_line(self, line_id: int) -> dict | None:
        return self.q1("SELECT * FROM lines WHERE id=?", (line_id,))

    def add_line(self, name: str) -> dict:
        name = _need(name, "line name")
        f = self.ensure_factory()
        try:
            lid = self.x("INSERT INTO lines(factory_id, name) VALUES(?,?)", (f["id"], name))
        except sqlite3.IntegrityError:
            raise StoreError(f"line '{name}' already exists") from None
        return self.get_line(lid)

    def rename_line(self, line_id: int, name: str) -> dict:
        name = _need(name, "line name")
        if not self.get_line(line_id):
            raise StoreError("no such line")
        try:
            self.x("UPDATE lines SET name=? WHERE id=?", (name, line_id))
        except sqlite3.IntegrityError:
            raise StoreError(f"line '{name}' already exists") from None
        return self.get_line(line_id)

    # --------------------------------------------------------------- products
    def list_products(self) -> list[dict]:
        return self.q("SELECT * FROM products ORDER BY id")

    def get_product(self, product_id: int) -> dict | None:
        return self.q1("SELECT * FROM products WHERE id=?", (product_id,))

    def product_by_code(self, code: str) -> dict | None:
        return self.q1("SELECT * FROM products WHERE code=? COLLATE NOCASE", (code,))

    def add_product(self, code: str, name: str, target_cycle_min: float | None = None) -> dict:
        code = _need(code, "product code").upper()
        if not code.replace("-", "").replace("_", "").isalnum():
            raise StoreError("product code may only contain letters, digits, '-' and '_'")
        name = _need(name, "product name")
        _positive(target_cycle_min, "target production time")
        try:
            pid = self.x("INSERT INTO products(code, name, target_cycle_min) VALUES(?,?,?)",
                         (code, name, target_cycle_min))
        except sqlite3.IntegrityError:
            raise StoreError(f"product code '{code}' already exists") from None
        return self.get_product(pid)

    def update_product(self, product_id: int, name: str | None = None,
                       target_cycle_min: float | None = None, clear_target: bool = False) -> dict:
        if not self.get_product(product_id):
            raise StoreError("no such product")
        _positive(target_cycle_min, "target production time")
        with self.tx() as c:
            if name:
                c.execute("UPDATE products SET name=? WHERE id=?", (name.strip(), product_id))
            if clear_target:
                c.execute("UPDATE products SET target_cycle_min=NULL WHERE id=?", (product_id,))
            elif target_cycle_min is not None:
                c.execute("UPDATE products SET target_cycle_min=? WHERE id=?", (target_cycle_min, product_id))
        return self.get_product(product_id)

    # --------------------------------------------------------------- stations
    def list_stations(self, line_id: int | None = None) -> list[dict]:
        rows = self.q("SELECT * FROM stations" + (" WHERE line_id=?" if line_id else "") + " ORDER BY id",
                      (line_id,) if line_id else ())
        return [self._station(r) for r in rows]

    def get_station(self, station_id: int) -> dict | None:
        r = self.q1("SELECT * FROM stations WHERE id=?", (station_id,))
        return self._station(r) if r else None

    @staticmethod
    def _station(r: dict) -> dict:
        r = dict(r)
        r["zone"] = json.loads(r["zone"]) if r.get("zone") else None
        return r

    def add_station(self, line_id: int, name: str, kind: str = "process", **opts: Any) -> dict:
        if not self.get_line(line_id):
            raise StoreError("no such line")
        name = _need(name, "station name")
        if kind not in STATION_KINDS:
            raise StoreError(f"kind must be one of {', '.join(STATION_KINDS)}")
        vals = self._station_opts(opts)
        try:
            sid = self.x(
                "INSERT INTO stations(line_id, name, kind, camera_id, zone, expected_process_min, "
                "expected_wait_qty, target_units_per_h, reject_threshold_pct, idle_alert_min, capacity) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (line_id, name, kind, vals.get("camera_id"), json.dumps(vals["zone"]) if vals.get("zone") else None,
                 vals.get("expected_process_min"), vals.get("expected_wait_qty", 10),
                 vals.get("target_units_per_h"), vals.get("reject_threshold_pct", 5.0),
                 vals.get("idle_alert_min", 25.0), vals.get("capacity", 1)))
        except sqlite3.IntegrityError:
            raise StoreError(f"station '{name}' already exists on this line") from None
        return self.get_station(sid)

    def update_station(self, station_id: int, **opts: Any) -> dict:
        st = self.get_station(station_id)
        if not st:
            raise StoreError("no such station")
        name = opts.pop("name", None)
        clear = set(opts.pop("clear", []) or [])
        vals = self._station_opts(opts)
        sets, params = [], []
        if name:
            sets.append("name=?"); params.append(_need(name, "station name"))
        for k in ("camera_id", "expected_process_min", "expected_wait_qty", "target_units_per_h",
                  "reject_threshold_pct", "idle_alert_min", "capacity"):
            if k in clear:
                sets.append(f"{k}=NULL" if k in ("camera_id", "expected_process_min", "target_units_per_h")
                            else f"{k}=?")
                if k not in ("camera_id", "expected_process_min", "target_units_per_h"):
                    params.append({"expected_wait_qty": 10, "reject_threshold_pct": 5.0, "idle_alert_min": 25.0,
                                   "capacity": 1}[k])
            elif k in vals:
                sets.append(f"{k}=?"); params.append(vals[k])
        if "zone" in clear:
            sets.append("zone=NULL")
        elif "zone" in vals:
            sets.append("zone=?"); params.append(json.dumps(vals["zone"]))
        if sets:
            try:
                self.x(f"UPDATE stations SET {', '.join(sets)} WHERE id=?", (*params, station_id))
            except sqlite3.IntegrityError:
                raise StoreError("that station name already exists on this line") from None
        return self.get_station(station_id)

    def _station_opts(self, opts: dict) -> dict:
        out: dict[str, Any] = {}
        for k in ("expected_process_min", "target_units_per_h", "reject_threshold_pct", "idle_alert_min"):
            if opts.get(k) is not None:
                _positive(opts[k], k.replace("_", " "), allow_zero=(k == "reject_threshold_pct"))
                out[k] = float(opts[k])
        if opts.get("capacity") is not None:
            try:
                cap = int(opts["capacity"])
            except (TypeError, ValueError):
                raise StoreError("capacity must be a whole number of machines/positions") from None
            if not 1 <= cap <= 500:
                raise StoreError("capacity must be between 1 and 500")
            out["capacity"] = cap
        if opts.get("expected_wait_qty") is not None:
            if int(opts["expected_wait_qty"]) < 0:
                raise StoreError("expected waiting quantity can't be negative")
            out["expected_wait_qty"] = int(opts["expected_wait_qty"])
        if opts.get("reject_threshold_pct") is not None and float(opts["reject_threshold_pct"]) > 100:
            raise StoreError("rejection threshold is a percentage (0-100)")
        if opts.get("camera_id") is not None:
            if not self.get_camera(int(opts["camera_id"])):
                raise StoreError("no such camera")
            out["camera_id"] = int(opts["camera_id"])
        if opts.get("zone") is not None:
            out["zone"] = validate_polygon(opts["zone"])
        return out

    def delete_station(self, station_id: int) -> None:
        if self.scalar("SELECT COUNT(*) FROM route_steps WHERE station_id=?", (station_id,), 0):
            raise StoreError("station is used by a route - remove it from the route first")
        if self.scalar("SELECT COUNT(*) FROM visits WHERE station_id=?", (station_id,), 0):
            raise StoreError("station has production history and can't be deleted")
        self.x("DELETE FROM stations WHERE id=?", (station_id,))

    # ----------------------------------------------------------------- routes
    def list_routes(self, line_id: int | None = None) -> list[dict]:
        rows = self.q("SELECT r.*, p.code AS product_code, p.name AS product_name, p.target_cycle_min "
                      "FROM routes r JOIN products p ON p.id=r.product_id"
                      + (" WHERE r.line_id=?" if line_id else "") + " ORDER BY r.id",
                      (line_id,) if line_id else ())
        for r in rows:
            r["steps"] = self.route_steps(r["id"])
        return rows

    def get_route(self, route_id: int) -> dict | None:
        rows = [r for r in self.list_routes() if r["id"] == route_id]
        return rows[0] if rows else None

    def route_steps(self, route_id: int) -> list[dict]:
        return self.q("SELECT rs.seq, rs.station_id, rs.expected_process_min AS step_expected_min, "
                      "s.name AS station_name, s.kind, s.expected_process_min AS station_expected_min "
                      "FROM route_steps rs JOIN stations s ON s.id=rs.station_id "
                      "WHERE rs.route_id=? ORDER BY rs.seq", (route_id,))

    def save_route(self, line_id: int, product_id: int, station_ids: list[int],
                   step_minutes: dict[int, float] | None = None) -> dict:
        """Create or replace the route for (line, product). Stations must be on the line,
        first = source, last = sink, everything between = process, no repeats (rework
        loops aren't supported yet). Replacing a route that has units is refused."""
        if not self.get_line(line_id):
            raise StoreError("no such line")
        if not self.get_product(product_id):
            raise StoreError("no such product")
        if len(station_ids) < 3:
            raise StoreError("a route needs a source, at least one process step and a sink")
        if len(set(station_ids)) != len(station_ids):
            raise StoreError("a station can appear only once in a route")
        sts = []
        for sid in station_ids:
            st = self.get_station(int(sid))
            if not st or st["line_id"] != line_id:
                raise StoreError(f"station {sid} isn't on this line")
            sts.append(st)
        if sts[0]["kind"] != "source" or sts[-1]["kind"] != "sink":
            raise StoreError("the first step must be a source station and the last a sink station")
        if any(s["kind"] != "process" for s in sts[1:-1]):
            raise StoreError("steps between source and sink must be process stations")
        prod = self.get_product(product_id)
        existing = self.q1("SELECT id FROM routes WHERE line_id=? AND product_id=?", (line_id, product_id))
        with self.tx() as c:
            if existing:
                if c.execute("SELECT COUNT(*) FROM units WHERE route_id=?", (existing["id"],)).fetchone()[0]:
                    raise StoreError("this route already has production history - create a new product/route instead")
                rid = existing["id"]
                c.execute("DELETE FROM route_steps WHERE route_id=?", (rid,))
            else:
                rid = c.execute("INSERT INTO routes(line_id, product_id, name) VALUES(?,?,?)",
                                (line_id, product_id, f"{prod['name']} route")).lastrowid
            for i, st in enumerate(sts):
                mins = (step_minutes or {}).get(st["id"])
                _positive(mins, "step time")
                c.execute("INSERT INTO route_steps(route_id, seq, station_id, expected_process_min) VALUES(?,?,?,?)",
                          (rid, i, st["id"], mins))
        return self.get_route(rid)

    # --------------------------------------------------------------- cameras
    def list_cameras(self) -> list[dict]:
        return [self._camera(r) for r in self.q("SELECT * FROM cameras ORDER BY id")]

    def get_camera(self, camera_id: int) -> dict | None:
        r = self.q1("SELECT * FROM cameras WHERE id=?", (camera_id,))
        return self._camera(r) if r else None

    @staticmethod
    def _camera(r: dict) -> dict:
        r = dict(r)
        r["params"] = json.loads(r.get("params") or "{}")
        r["decode_qr"] = bool(r["decode_qr"])
        r["enabled"] = bool(r["enabled"])
        return r

    def add_camera(self, name: str, source: str, line_id: int | None = None, detector: str = "motion",
                   params: dict | None = None, decode_qr: bool = False, enabled: bool = True) -> dict:
        name, source = _need(name, "camera name"), _need(source, "camera source")
        if detector not in ("motion", "yolo"):
            raise StoreError("detector must be 'motion' or 'yolo'")
        if line_id is not None and not self.get_line(line_id):
            raise StoreError("no such line")
        try:
            cid = self.x("INSERT INTO cameras(name, source, line_id, detector, params, decode_qr, enabled) "
                         "VALUES(?,?,?,?,?,?,?)",
                         (name, source, line_id, detector, json.dumps(params or {}), int(decode_qr), int(enabled)))
        except sqlite3.IntegrityError:
            raise StoreError(f"camera '{name}' already exists") from None
        return self.get_camera(cid)

    def update_camera(self, camera_id: int, **opts: Any) -> dict:
        cam = self.get_camera(camera_id)
        if not cam:
            raise StoreError("no such camera")
        sets, params = [], []
        for k in ("name", "source", "line_id", "detector"):
            if opts.get(k) is not None:
                if k == "detector" and opts[k] not in ("motion", "yolo"):
                    raise StoreError("detector must be 'motion' or 'yolo'")
                if k == "line_id" and not self.get_line(int(opts[k])):
                    raise StoreError("no such line")
                sets.append(f"{k}=?"); params.append(opts[k])
        if opts.get("params") is not None:
            sets.append("params=?"); params.append(json.dumps(opts["params"]))
        for k in ("decode_qr", "enabled"):
            if opts.get(k) is not None:
                sets.append(f"{k}=?"); params.append(int(bool(opts[k])))
        if sets:
            try:
                self.x(f"UPDATE cameras SET {', '.join(sets)} WHERE id=?", (*params, camera_id))
            except sqlite3.IntegrityError:
                raise StoreError("that camera name already exists") from None
        return self.get_camera(camera_id)

    def delete_camera(self, camera_id: int) -> None:
        with self.tx() as c:
            c.execute("UPDATE stations SET camera_id=NULL, zone=NULL WHERE camera_id=?", (camera_id,))
            c.execute("DELETE FROM cameras WHERE id=?", (camera_id,))

    # ---------------------------------------------------------------- batches
    def list_batches(self, line_id: int | None = None, status: str | None = None, limit: int = 200) -> list[dict]:
        where, p = [], []
        if line_id:
            where.append("b.line_id=?"); p.append(line_id)
        if status:
            where.append("b.status=?"); p.append(status)
        return self.q(
            "SELECT b.*, p.code AS product_code, p.name AS product_name, l.name AS line_name, "
            "(SELECT COUNT(*) FROM units u WHERE u.batch_id=b.id) AS issued, "
            "(SELECT COUNT(*) FROM units u WHERE u.batch_id=b.id AND u.status='finished') AS finished, "
            "(SELECT COUNT(*) FROM units u WHERE u.batch_id=b.id AND u.status='rejected') AS rejected, "
            "(SELECT COUNT(*) FROM units u WHERE u.batch_id=b.id AND u.status='in_progress') AS in_progress "
            "FROM batches b JOIN products p ON p.id=b.product_id JOIN lines l ON l.id=b.line_id "
            + ("WHERE " + " AND ".join(where) if where else "") + " ORDER BY b.created_at DESC, b.id DESC LIMIT ?",
            (*p, limit))

    def get_batch(self, batch_id: int) -> dict | None:
        r = self.q1("SELECT * FROM batches WHERE id=?", (batch_id,))
        return r

    def batch_by_code(self, code: str) -> dict | None:
        return self.q1("SELECT * FROM batches WHERE code=? COLLATE NOCASE", (code,))

    def add_batch(self, product_id: int, line_id: int, qty_planned: int | None = None,
                  code: str | None = None, created_at: float | None = None) -> dict:
        route = self.q1("SELECT id FROM routes WHERE line_id=? AND product_id=?", (line_id, product_id))
        if not route:
            raise StoreError("no route is configured for that product on that line (Setup → Routes)")
        if qty_planned is not None and int(qty_planned) <= 0:
            raise StoreError("planned quantity must be positive")
        ts = created_at or time.time()
        if not code:
            day = time.strftime("%Y-%m%d", time.localtime(ts))
            n = self.scalar("SELECT COUNT(*) FROM batches WHERE code LIKE ?", (f"BATCH-{day}-%",), 0) + 1
            code = f"BATCH-{day}-{n:03d}"
            while self.batch_by_code(code):
                n += 1
                code = f"BATCH-{day}-{n:03d}"
        try:
            bid = self.x("INSERT INTO batches(code, product_id, line_id, route_id, qty_planned, status, created_at) "
                         "VALUES(?,?,?,?,?,'active',?)",
                         (code.strip().upper(), product_id, line_id, route["id"], qty_planned, ts))
        except sqlite3.IntegrityError:
            raise StoreError(f"batch '{code}' already exists") from None
        return self.get_batch(bid)

    def close_batch(self, batch_id: int, ts: float | None = None) -> dict:
        if not self.get_batch(batch_id):
            raise StoreError("no such batch")
        self.x("UPDATE batches SET status='closed', closed_at=? WHERE id=? AND status='active'",
               (ts or time.time(), batch_id))
        return self.get_batch(batch_id)

    def active_batch(self, line_id: int, product_id: int | None = None) -> dict | None:
        return self.q1("SELECT * FROM batches WHERE line_id=? AND status='active'"
                       + (" AND product_id=?" if product_id else "") + " ORDER BY created_at DESC, id DESC LIMIT 1",
                       (line_id, product_id) if product_id else (line_id,))

    # ----------------------------------------------------------------- alerts
    def list_alerts(self, status: str | None = None, limit: int = 200) -> list[dict]:
        rows = self.q("SELECT a.*, s.name AS station_name FROM alerts a LEFT JOIN stations s ON s.id=a.station_id"
                      + (" WHERE a.status=?" if status else "")
                      + " ORDER BY a.status='resolved', a.updated_at DESC LIMIT ?",
                      (status, limit) if status else (limit,))
        for r in rows:
            r["evidence"] = json.loads(r["evidence"] or "{}")
        return rows

    def ack_alert(self, alert_id: int) -> dict:
        a = self.q1("SELECT * FROM alerts WHERE id=?", (alert_id,))
        if not a:
            raise StoreError("no such alert")
        if a["status"] == "open":
            self.x("UPDATE alerts SET status='ack', ack_at=?, updated_at=? WHERE id=?",
                   (time.time(), time.time(), alert_id))
        return self.q1("SELECT * FROM alerts WHERE id=?", (alert_id,))

    # ------------------------------------------------------------- data quality
    def event_counts(self, since: float, until: float | None = None) -> dict[str, int]:
        rows = self.q("SELECT result, COUNT(*) n FROM events WHERE ts>=? AND ts<? GROUP BY result",
                      (since, until if until is not None else time.time() + 3600))
        return {r["result"]: r["n"] for r in rows}


# ------------------------------------------------------------------ validation
def _need(v: Any, what: str) -> str:
    v = (v or "").strip() if isinstance(v, str) else v
    if not v:
        raise StoreError(f"{what} is required")
    return v


def _positive(v: Any, what: str, allow_zero: bool = False) -> None:
    if v is None:
        return
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise StoreError(f"{what} must be a number") from None
    if f != f or f in (float("inf"), float("-inf")) or f < 0 or (f == 0 and not allow_zero):
        raise StoreError(f"{what} must be greater than 0")


def validate_polygon(points: Any) -> list[list[float]]:
    """A zone is a polygon in normalised (0-1) image coordinates so it survives resolution changes."""
    if not isinstance(points, list) or len(points) < 3:
        raise StoreError("a zone needs at least 3 points")
    out = []
    for p in points:
        try:
            x, y = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError):
            raise StoreError("zone points must be [x, y] pairs") from None
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise StoreError("zone points must be between 0 and 1 (fractions of the image)")
        out.append([round(x, 4), round(y, 4)])
    return out


def validate_shifts(shifts: Any) -> None:
    if not isinstance(shifts, list) or not shifts:
        raise StoreError("define at least one shift")
    names = set()
    for s in shifts:
        try:
            name, a, b = str(s["name"]).strip(), str(s["start"]), str(s["end"])
            for t in (a, b):
                hh, mm = t.split(":")
                if not (0 <= int(hh) <= 23 and 0 <= int(mm) <= 59):
                    raise ValueError
        except (KeyError, ValueError, TypeError):
            raise StoreError("each shift needs a name and start/end as HH:MM") from None
        if not name or name in names:
            raise StoreError("shift names must be unique and non-empty")
        if a == b:
            raise StoreError(f"shift '{name}' starts and ends at the same time")
        names.add(name)
