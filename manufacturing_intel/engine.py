"""Production tracking engine: turns stage events into unit journeys.

Events (all go through `ProductionEngine.record`):

    issue / enter @ source   raw material released -> a unit is created
    enter          @ process processing starts  (unit enters the machine zone)
    exit           @ process processing ends    (unit now waits for the next station)
    reject         @ process unit scrapped here (with a reason)
    enter          @ sink    unit finished      (automatic when the sink has no camera/feed)

processing time = exit - enter at a station
waiting time    = enter at a station - exit of the previous station

Events carry a unit code when the source knows it (barcode / QR / PLC). Without a code
the engine matches FIFO along the line - the oldest unit waiting for this station on
enter, the oldest unit processing here on exit. That's an assumption about the line,
so anything that doesn't fit (a duplicate, an event with nobody to match, going
backwards in the route) is *logged with its result and left alone*, never repaired
silently: `events.result` = applied | ignored | unmatched | error.

Every call is one SQLite transaction, and rejected events roll back to a savepoint
before being logged, so a refused event can never leave a half-updated unit.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any, Callable

from .store import REJECT_REASONS, Store

EVENT_TYPES = ("issue", "enter", "exit", "reject")
FUTURE_TOLERANCE_S = 60.0


class Outcome(Exception):
    """An expected, non-fatal result: the event is logged and nothing is changed."""

    def __init__(self, result: str, detail: str) -> None:
        super().__init__(detail)
        self.result, self.detail = result, detail


def _rows(c: sqlite3.Connection, sql: str, p: tuple = ()) -> list[dict]:
    return [dict(r) for r in c.execute(sql, p).fetchall()]


def _row(c: sqlite3.Connection, sql: str, p: tuple = ()) -> dict | None:
    r = c.execute(sql, p).fetchone()
    return dict(r) if r else None


class ProductionEngine:
    def __init__(self, store: Store, clock: Callable[[], float] = time.time) -> None:
        self.store = store
        self.clock = clock

    # ------------------------------------------------------------------ public
    def record(self, type: str, station_id: int, *, ts: float | None = None, unit_code: str | None = None,
               product: str | int | None = None, batch: str | None = None, reason: str | None = None,
               source: str = "api", camera_id: int | None = None) -> dict:
        """Apply one event. Returns {result, detail, unit_code, unit_id, event_id}."""
        now = self.clock()
        try:
            ts = float(ts) if ts is not None else now
        except (TypeError, ValueError):
            return {"result": "error", "detail": "timestamp must be a number (epoch seconds)"}
        if ts != ts or ts < 946684800:
            return {"result": "error", "detail": "timestamp is not a valid epoch time"}
        if type not in EVENT_TYPES:
            return {"result": "error", "detail": f"type must be one of {', '.join(EVENT_TYPES)}"}
        st = self.store.get_station(int(station_id))
        if not st:
            return {"result": "error", "detail": f"unknown station {station_id}"}
        code = (unit_code or "").strip().upper() or None
        if ts > now + FUTURE_TOLERANCE_S:
            with self.store.tx() as c:
                eid = self._log(c, ts, type, st, None, code, source, camera_id, "error", "timestamp is in the future", reason)
            return {"result": "error", "detail": "timestamp is in the future", "event_id": eid}

        reason_norm, reason_note = None, None
        if type == "reject":
            r = (reason or "other").strip().lower().replace(" ", "_")
            reason_norm = r if r in REJECT_REASONS else "other"
            reason_note = None if r in REJECT_REASONS else f"reason '{reason}'"

        with self.store.tx() as c:
            c.execute("SAVEPOINT ev")
            unit: dict | None = None
            try:
                unit, detail = self._apply(c, st, type, ts, code, product, batch, reason_norm, source)
                result = "applied"
                if reason_note:
                    detail = f"{detail}; {reason_note}" if detail else reason_note
                c.execute("RELEASE ev")
            except Outcome as o:
                c.execute("ROLLBACK TO ev")
                c.execute("RELEASE ev")
                result, detail = o.result, o.detail
            eid = self._log(c, ts, type, st, unit["id"] if unit else None, unit["code"] if unit else code,
                            source, camera_id, result, detail, reason_norm)
        return {"result": result, "detail": detail, "unit_code": unit["code"] if unit else code,
                "unit_id": unit["id"] if unit else None, "event_id": eid}

    # ---------------------------------------------------------------- internals
    def _log(self, c, ts, type, st, unit_id, unit_code, source, camera_id, result, detail, reason) -> int:
        return c.execute(
            "INSERT INTO events(ts, received_at, type, station_id, unit_id, unit_code, source, camera_id, result, detail, reason) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (ts, self.clock(), type, st["id"], unit_id, unit_code, source, camera_id, result, detail, reason)).lastrowid

    def _apply(self, c, st: dict, type: str, ts: float, code: str | None, product, batch, reason: str | None,
               source: str) -> tuple[dict | None, str]:
        kind = st["kind"]
        if kind == "source":
            if type in ("issue", "enter"):
                if code and _row(c, "SELECT id FROM units WHERE code=?", (code,)):
                    raise Outcome("ignored", f"{code} was already issued")
                unit = self._create_unit(c, st, ts, code, product, batch, source, at_source=True)
                return unit, "unit issued"
            raise Outcome("ignored", f"'{type}' isn't tracked at a source station (use issue/enter)")
        if type == "issue":
            raise Outcome("ignored", "'issue' only applies to a source station")
        if kind == "sink" and type != "enter":
            raise Outcome("ignored", f"'{type}' isn't tracked at a sink station (finishing is an 'enter')")

        unit = self._resolve_unit(c, st, type, code, product, batch, source, ts)
        if unit["status"] != "in_progress":
            raise Outcome("ignored", f"{unit['code']} is already {unit['status']}")
        steps = _rows(c, "SELECT rs.seq, rs.station_id, s.kind, s.camera_id FROM route_steps rs "
                         "JOIN stations s ON s.id=rs.station_id WHERE rs.route_id=? ORDER BY rs.seq", (unit["route_id"],))
        k = next((s["seq"] for s in steps if s["station_id"] == st["id"]), None)
        if k is None:
            raise Outcome("ignored", f"{st['name']} isn't on {unit['code']}'s route")
        self._check_order(c, unit, ts)

        if type == "enter" and kind == "sink":
            return self._finish_at_sink(c, unit, st, k, ts, steps)
        if type == "enter":
            return self._enter(c, unit, st, k, ts)
        if type == "exit":
            return self._exit(c, unit, st, k, ts, steps)
        return self._reject(c, unit, st, k, ts, reason)

    # --- unit resolution ---------------------------------------------------
    def _resolve_unit(self, c, st, type, code, product, batch, source, ts) -> dict:
        if code:
            unit = _row(c, "SELECT * FROM units WHERE code=?", (code,))
            if unit:
                return unit
            if type == "enter" and self._is_first_process(c, st):
                return self._create_unit(c, st, ts, code, product, batch, source, at_source=False)
            raise Outcome("unmatched", f"unknown unit {code}")
        # anonymous -> FIFO along the line
        outcomes = {"enter": ("waiting",), "exit": ("processing",), "reject": ("processing", "waiting")}[type]
        for outcome in outcomes:
            order = "v.start_ts" if outcome == "processing" else "v.arrived_ts"
            row = _row(c, "SELECT u.* FROM visits v JOIN units u ON u.id=v.unit_id "
                          f"WHERE v.station_id=? AND v.outcome=? AND u.status='in_progress' "
                          f"ORDER BY {order}, v.id LIMIT 1", (st["id"], outcome))
            if row:
                return row
        if type == "enter" and st["kind"] == "process" and self._is_first_process(c, st):
            return self._create_unit(c, st, ts, None, product, batch, source, at_source=False)
        what = {"enter": "waiting for", "exit": "processing at", "reject": "at"}[type]
        raise Outcome("unmatched", f"no unit {what} {st['name']} to match this '{type}'")

    @staticmethod
    def _is_first_process(c, st: dict) -> bool:
        return bool(_row(c, "SELECT 1 FROM route_steps WHERE station_id=? AND seq=1", (st["id"],)))

    def _create_unit(self, c, st, ts, code, product, batch_code, source, at_source: bool) -> dict:
        line_id = st["line_id"]
        b = self._pick_batch(c, line_id, product, batch_code, ts)
        steps = _rows(c, "SELECT rs.seq, rs.station_id FROM route_steps rs WHERE rs.route_id=? ORDER BY rs.seq", (b["route_id"],))
        expect = steps[0]["station_id"] if at_source else steps[1]["station_id"]
        if st["id"] != expect:
            raise Outcome("unmatched", f"{st['name']} isn't the "
                                       f"{'source' if at_source else 'first process step'} of batch {b['code']}'s route")
        prod = _row(c, "SELECT code FROM products WHERE id=?", (b["product_id"],))
        if code is None:
            n = c.execute("SELECT COUNT(*) FROM units WHERE product_id=?", (b["product_id"],)).fetchone()[0] + 1
            while _row(c, "SELECT 1 FROM units WHERE code=?", (f"{prod['code']}-{n:06d}",)):
                n += 1
            code = f"{prod['code']}-{n:06d}"
        uid = c.execute("INSERT INTO units(code, batch_id, product_id, line_id, route_id, status, step_seq, created_at, source) "
                        "VALUES(?,?,?,?,?,'in_progress',1,?,?)",
                        (code, b["id"], b["product_id"], line_id, b["route_id"], ts, source)).lastrowid
        # source visit: instantaneous; the unit then waits for the first process step
        c.execute("INSERT INTO visits(unit_id, station_id, step_seq, arrived_ts, start_ts, end_ts, outcome, processing_s, waiting_s) "
                  "VALUES(?,?,0,?,?,?,'completed',0,0)", (uid, steps[0]["station_id"], ts, ts, ts))
        c.execute("INSERT INTO visits(unit_id, station_id, step_seq, arrived_ts, outcome) VALUES(?,?,1,?,'waiting')",
                  (uid, steps[1]["station_id"], ts))
        return _row(c, "SELECT * FROM units WHERE id=?", (uid,))

    def _pick_batch(self, c, line_id: int, product, batch_code: str | None, ts: float) -> dict:
        if batch_code:
            b = _row(c, "SELECT * FROM batches WHERE code=? COLLATE NOCASE", (batch_code.strip(),))
            if not b or b["line_id"] != line_id:
                raise Outcome("unmatched", f"batch '{batch_code}' not found on this line")
            if b["status"] != "active":
                raise Outcome("ignored", f"batch {b['code']} is closed")
            return b
        pid = self._product_id(c, product)
        if product is not None and pid is None:
            raise Outcome("unmatched", f"unknown product '{product}'")
        b = _row(c, "SELECT * FROM batches WHERE line_id=? AND status='active'" + (" AND product_id=?" if pid else "")
                 + " ORDER BY created_at DESC, id DESC LIMIT 1", (line_id, pid) if pid else (line_id,))
        if b:
            return b
        routes = _rows(c, "SELECT * FROM routes WHERE line_id=?" + (" AND product_id=?" if pid else ""),
                       (line_id, pid) if pid else (line_id,))
        if len(routes) != 1:
            raise Outcome("unmatched", "no active batch, and the product can't be inferred "
                                       "(start a batch or send the product code)")
        r = routes[0]
        prod = _row(c, "SELECT code FROM products WHERE id=?", (r["product_id"],))
        day = time.strftime("%Y-%m%d", time.localtime(ts))
        code = f"AUTO-{prod['code']}-{day}"
        b = _row(c, "SELECT * FROM batches WHERE code=?", (code,))
        if b and b["status"] == "active":
            return b
        if b:  # closed auto batch of the same day: open a numbered one
            n = c.execute("SELECT COUNT(*) FROM batches WHERE code LIKE ?", (code + "%",)).fetchone()[0] + 1
            code = f"{code}-{n}"
        bid = c.execute("INSERT INTO batches(code, product_id, line_id, route_id, status, created_at) VALUES(?,?,?,?,'active',?)",
                        (code, r["product_id"], line_id, r["id"], ts)).lastrowid
        return _row(c, "SELECT * FROM batches WHERE id=?", (bid,))

    @staticmethod
    def _product_id(c, product) -> int | None:
        if product is None or product == "":
            return None
        if isinstance(product, int) or str(product).isdigit():
            r = _row(c, "SELECT id FROM products WHERE id=?", (int(product),))
        else:
            r = _row(c, "SELECT id FROM products WHERE code=? COLLATE NOCASE", (str(product).strip(),))
        return r["id"] if r else None

    # --- transitions ---------------------------------------------------------
    @staticmethod
    def _visit(c, unit_id: int, seq: int) -> dict | None:
        return _row(c, "SELECT * FROM visits WHERE unit_id=? AND step_seq=?", (unit_id, seq))

    def _check_order(self, c, unit: dict, ts: float) -> None:
        last = c.execute(
            "SELECT MAX(MAX(COALESCE(arrived_ts,0), COALESCE(start_ts,0), COALESCE(end_ts,0))) FROM visits WHERE unit_id=?",
            (unit["id"],)).fetchone()[0] or unit["created_at"]
        if ts < last - 0.001:
            raise Outcome("error", f"out of order: {unit['code']} already has an event later than this one")

    def _skip_to(self, c, unit: dict, k: int, ts: float) -> str:
        """The unit shows up at step k although earlier steps never reported: close them as
        'skipped' (no times invented) and flag it in the event detail."""
        skipped = list(range(unit["step_seq"], k))
        for seq in skipped:
            v = self._visit(c, unit["id"], seq)
            st_id = c.execute("SELECT station_id FROM route_steps WHERE route_id=? AND seq=?",
                              (unit["route_id"], seq)).fetchone()[0]
            if v:
                c.execute("UPDATE visits SET outcome='skipped', end_ts=?, processing_s=NULL WHERE id=?", (ts, v["id"]))
            else:
                c.execute("INSERT INTO visits(unit_id, station_id, step_seq, end_ts, outcome) VALUES(?,?,?,?,'skipped')",
                          (unit["id"], st_id, seq, ts))
        st_k = c.execute("SELECT station_id FROM route_steps WHERE route_id=? AND seq=?", (unit["route_id"], k)).fetchone()[0]
        c.execute("INSERT INTO visits(unit_id, station_id, step_seq, arrived_ts, outcome) VALUES(?,?,?,?,'waiting')",
                  (unit["id"], st_k, k, ts))
        c.execute("UPDATE units SET step_seq=? WHERE id=?", (k, unit["id"]))
        unit["step_seq"] = k
        return "skipped steps " + ",".join(str(s) for s in skipped)

    def _enter(self, c, unit, st, k, ts) -> tuple[dict, str]:
        note = ""
        if k < unit["step_seq"]:
            raise Outcome("ignored", f"out of route: {unit['code']} is already past {st['name']}")
        if k > unit["step_seq"]:
            note = self._skip_to(c, unit, k, ts)
            # the arrival time at k is unknown -> waiting time is unknown (NULL), not 0
            c.execute("UPDATE visits SET arrived_ts=NULL WHERE unit_id=? AND step_seq=?", (unit["id"], k))
        v = self._visit(c, unit["id"], k)
        if v["outcome"] == "processing":
            raise Outcome("ignored", f"duplicate enter: {unit['code']} is already in {st['name']}")
        waiting = (ts - v["arrived_ts"]) if v["arrived_ts"] is not None else None
        c.execute("UPDATE visits SET start_ts=?, waiting_s=?, outcome='processing' WHERE id=?", (ts, waiting, v["id"]))
        if v["arrived_ts"] is None:
            c.execute("UPDATE visits SET arrived_ts=? WHERE id=?", (ts, v["id"]))
        return unit, note

    def _exit(self, c, unit, st, k, ts, steps) -> tuple[dict, str]:
        if k < unit["step_seq"]:
            raise Outcome("ignored", f"duplicate exit: {unit['code']} already left {st['name']}")
        v = self._visit(c, unit["id"], k) if k == unit["step_seq"] else None
        if v is None or v["outcome"] != "processing":
            raise Outcome("unmatched", f"exit without a recorded enter at {st['name']} for {unit['code']}")
        if ts < v["start_ts"]:
            raise Outcome("error", "exit is earlier than the enter")
        c.execute("UPDATE visits SET end_ts=?, processing_s=?, outcome='completed' WHERE id=?",
                  (ts, ts - v["start_ts"], v["id"]))
        nk = k + 1
        nxt = steps[nk]
        c.execute("UPDATE units SET step_seq=? WHERE id=?", (nk, unit["id"]))
        c.execute("INSERT INTO visits(unit_id, station_id, step_seq, arrived_ts, outcome) VALUES(?,?,?,?,'waiting')",
                  (unit["id"], nxt["station_id"], nk, ts))
        note = ""
        if nxt["kind"] == "sink" and nxt["camera_id"] is None:
            self._complete_sink(c, unit, nk, ts)
            note = "finished"
        return unit, note

    def _finish_at_sink(self, c, unit, st, k, ts, steps) -> tuple[dict, str]:
        if k < unit["step_seq"]:
            raise Outcome("ignored", f"{unit['code']} already finished")
        note = ""
        if k > unit["step_seq"]:
            note = self._skip_to(c, unit, k, ts) + "; "
            c.execute("UPDATE visits SET arrived_ts=NULL WHERE unit_id=? AND step_seq=?", (unit["id"], k))
        self._complete_sink(c, unit, k, ts)
        return unit, note + "finished"

    def _complete_sink(self, c, unit, k, ts) -> None:
        v = self._visit(c, unit["id"], k)
        arrived = v["arrived_ts"]
        c.execute("UPDATE visits SET start_ts=?, end_ts=?, outcome='completed', processing_s=0, waiting_s=? WHERE id=?",
                  (ts, ts, (ts - arrived) if arrived is not None else None, v["id"]))
        c.execute("UPDATE units SET status='finished', finished_at=?, cycle_s=? WHERE id=?",
                  (ts, ts - unit["created_at"], unit["id"]))

    def _reject(self, c, unit, st, k, ts, reason) -> tuple[dict, str]:
        if st["kind"] != "process":
            raise Outcome("ignored", "rejects are recorded at process stations")
        if k < unit["step_seq"]:
            raise Outcome("ignored", f"out of route: {unit['code']} is already past {st['name']}")
        note = ""
        if k > unit["step_seq"]:
            note = self._skip_to(c, unit, k, ts)
            c.execute("UPDATE visits SET arrived_ts=NULL WHERE unit_id=? AND step_seq=?", (unit["id"], k))
        v = self._visit(c, unit["id"], k)
        processing = (ts - v["start_ts"]) if v["start_ts"] is not None else None
        if processing is not None and processing < 0:
            raise Outcome("error", "reject is earlier than the enter")
        c.execute("UPDATE visits SET end_ts=?, processing_s=?, outcome='rejected', reject_reason=? WHERE id=?",
                  (ts, processing, reason, v["id"]))
        c.execute("UPDATE units SET status='rejected', rejected_at=? WHERE id=?", (ts, unit["id"]))
        return unit, note
