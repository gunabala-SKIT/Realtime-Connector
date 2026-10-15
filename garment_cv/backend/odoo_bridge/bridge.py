"""The Odoo work-order bridge: camera activity in, Odoo work-order state changes out.

How the pieces fit (see `tracker.py` for the finish rules, `client.py` for the API):

    ActivityEngine --on_tick--> OdooBridge.on_tick --> OperationTracker --decisions--> outbox (SQLite)
                                                                                         |
          background thread, every `poll_s`:  reconcile with Odoo  <--  OdooClient  <----+ drain

* Odoo is the source of truth. The tracker acts optimistically on camera evidence, and every poll the
  bridge reads the real work-order states and the tracker adopts them - so a supervisor pressing a
  button in Odoo, a restart, or a failed call can never leave the two disagreeing for long.
* Decisions go through a persistent outbox, so an Odoo outage delays them instead of losing them, and a
  restart picks them up again. Odoo's start/finish calls are idempotent, so re-sending is safe.
  A transient failure (network, 5xx) is retried with back-off; a rejection (Odoo said no) stops that
  MO's queue and is shown to the operator - never retried blindly, never silently dropped.
* One MO per camera: only an MO the user has Start-ed (state `progress`) is acted on. Two such MOs at
  the same work center is a conflict - the bridge pauses and says so instead of guessing.
* Modes: `off` does nothing; `shadow` runs the whole decision logic and records what it WOULD do
  without writing to Odoo (use it to check behaviour against real work first); `live` writes.
  The mode can be switched at runtime (the kill switch) and is persisted across restarts.
* An MO is marked done only when every one of its work orders is done, never forcing other work
  centers' operations closed.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Callable

from ..database.db import Database
from .client import OdooClient, OdooError, OdooRejected, OdooTransient
from .config import MODES, BridgeConfig
from .tracker import ACTIVE, FINISHED, PENDING, Decision, OperationTracker, Step

_SCHEMA = """
CREATE TABLE IF NOT EXISTS odoo_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,             -- START | FINISH | MO_DONE
    production_id INTEGER NOT NULL,
    workorder_id INTEGER,
    step_name TEXT,
    reason TEXT,
    evidence TEXT,
    status TEXT NOT NULL DEFAULT 'pending',   -- pending | done | failed | cancelled
    attempts INTEGER NOT NULL DEFAULT 0,
    next_try REAL NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at REAL NOT NULL,
    done_at REAL
);
CREATE INDEX IF NOT EXISTS idx_outbox_status ON odoo_outbox(status);
CREATE TABLE IF NOT EXISTS odoo_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,               -- decision | applied | error | conflict | mode | manual | reconcile
    production_id INTEGER,
    workorder_id INTEGER,
    detail TEXT NOT NULL,
    shadow INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS odoo_kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

_ODOO_TO_STATE = {"progress": ACTIVE, "done": FINISHED, "cancel": FINISHED}
BACKOFF_S = (2, 5, 15, 30, 60)


class OdooBridge:
    def __init__(self, cfg: BridgeConfig, db: Database, log, client: OdooClient | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.cfg, self.db, self.log, self._clock = cfg, db, log, clock
        for stmt in [s for s in _SCHEMA.split(";") if s.strip()]:
            db.execute(stmt)
        self.client = client
        if self.client is None and cfg.configured:
            self.client = OdooClient(cfg.url, cfg.db, cfg.api_key, cfg.timeout_s)
        self._lock = threading.RLock()
        self.mode = self._saved_mode() or cfg.mode
        self.tracker: OperationTracker | None = None
        self.production_id: int | None = None
        self.production_name: str | None = None
        self._wo_ids: list[int] = []
        self.ticks = 0                               # engine updates received (live diagnostics)
        self.last_tick_wall = 0.0
        self.last_acts: list[str] = []
        self._odoo_seen: list[str] | None = None   # Odoo's work-order states at the last poll
        self.conflict: str | None = None
        self.last_error: str | None = None
        self.odoo_ok: bool | None = None
        self.last_poll: float = 0.0
        self.workcenter: dict | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ----------------------------------------------------------------------------- persistence
    def _saved_mode(self) -> str | None:
        rows = self.db.query("SELECT value FROM odoo_kv WHERE key='mode'")
        return rows[0]["value"] if rows and rows[0]["value"] in MODES else None

    def set_mode(self, mode: str, who: str = "operator") -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        with self._lock:
            old, self.mode = self.mode, mode
            self.db.execute("INSERT INTO odoo_kv(key,value) VALUES('mode',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (mode,))
            if mode == "off":
                self.tracker = None                 # re-learn everything from Odoo when switched back on
                self.production_id = None
        self._audit("mode", f"{who} changed mode {old} -> {mode}")

    def _audit(self, kind: str, detail: str, production_id: int | None = None, workorder_id: int | None = None) -> None:
        self.db.execute("INSERT INTO odoo_audit(ts,kind,production_id,workorder_id,detail,shadow) VALUES(?,?,?,?,?,?)",
                        (self._clock(), kind, production_id, workorder_id, detail, 1 if self.mode == "shadow" else 0))

    # ------------------------------------------------------------------------------ camera side
    def on_tick(self, camera_id: str, ts: float, activities: frozenset) -> None:
        """Engine callback (runs on the detection thread - must be quick and must never raise)."""
        try:
            self.ticks += 1
            self.last_tick_wall = self._clock()
            self.last_acts = sorted(activities)
            if self.mode == "off":
                return
            with self._lock:
                if self.tracker is None or self.conflict:
                    return
                decisions = self.tracker.observe(ts, activities)
                for d in decisions:
                    self._enqueue(d)
        except Exception:                                    # noqa: BLE001 - a bridge bug must never stop detection
            self.log.exception("odoo bridge on_tick failed")

    def _enqueue(self, d: Decision) -> None:
        step = self.tracker.steps[d.step]
        wo_id = self._wo_ids[d.step]
        ev = json.dumps(d.evidence)
        self._audit("decision", f"{d.kind} {step.name}: {d.reason}", self.production_id, wo_id)
        if self.mode != "live":
            return
        self._add_outbox(d.kind, wo_id, step.name, d.reason, ev)
        if d.kind == "FINISH" and d.step == len(self.tracker.steps) - 1:
            self._add_outbox("MO_DONE", None, step.name, "last operation finished", ev)

    def _add_outbox(self, action: str, wo_id: int | None, step_name: str, reason: str, evidence: str) -> None:
        # never queue the same pending action twice for the same work order (idempotent by construction)
        dup = self.db.query("SELECT 1 FROM odoo_outbox WHERE status='pending' AND action=? AND production_id=? "
                            "AND IFNULL(workorder_id,0)=IFNULL(?,0)", (action, self.production_id, wo_id))
        if not dup:
            self.db.execute("INSERT INTO odoo_outbox(action,production_id,workorder_id,step_name,reason,evidence,created_at) "
                            "VALUES(?,?,?,?,?,?,?)", (action, self.production_id, wo_id, step_name, reason, evidence, self._clock()))

    # ---------------------------------------------------------------------- operator controls
    def hold(self, on: bool) -> None:
        with self._lock:
            if self.tracker:
                self.tracker.held = bool(on)
        self._audit("manual", f"operator {'held' if on else 'released'} auto-finish", self.production_id)

    def manual(self, action: str, step: int) -> None:
        """Operator override from the dashboard: start or finish operation `step` right now."""
        with self._lock:
            if self.tracker is None or self.mode != "live":
                raise ValueError("bridge is not armed on an MO in live mode")
            if not 0 <= step < len(self._wo_ids):
                raise ValueError("no such step")
            action = action.upper()
            if action not in ("START", "FINISH"):
                raise ValueError("action must be start or finish")
            for k in range(step if action == "START" else step + 1):   # earlier operations must be done first
                if self.tracker.states[k] != FINISHED:
                    self._add_outbox("FINISH", self._wo_ids[k], self.tracker.steps[k].name, "manual override", "{}")
                    self.tracker.states[k] = FINISHED
            if action == "START":
                self._add_outbox("START", self._wo_ids[step], self.tracker.steps[step].name, "manual override", "{}")
                self.tracker.states[step] = ACTIVE
            else:
                if step == len(self._wo_ids) - 1:
                    self._add_outbox("MO_DONE", None, self.tracker.steps[step].name, "manual override", "{}")
            self._audit("manual", f"operator requested {action} of {self.tracker.steps[step].name}", self.production_id, self._wo_ids[step])

    def retry_failed(self) -> int:
        with self._lock:
            cur = self.db.execute("UPDATE odoo_outbox SET status='pending', attempts=0, next_try=0, last_error=NULL WHERE status='failed'")
        self._audit("manual", "operator retried failed actions")
        return cur.rowcount

    def discard_failed(self) -> int:
        cur = self.db.execute("UPDATE odoo_outbox SET status='cancelled' WHERE status='failed'")
        self._audit("manual", "operator discarded failed actions")
        return cur.rowcount

    # ----------------------------------------------------------------------------- Odoo side
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="odoo-bridge", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:                                # noqa: BLE001
                self.log.exception("odoo bridge poll failed")
            self._stop.wait(self.cfg.poll_s)

    def poll_once(self) -> None:
        """One reconcile + drain cycle (the background thread calls this; tests call it directly)."""
        if self.mode == "off" or self.client is None:
            return
        try:
            self._reconcile()
            if self.mode == "live":
                self._drain()
            self.odoo_ok = True
            self.last_error = None
        except OdooTransient as e:
            self.odoo_ok, self.last_error = False, str(e)
        except OdooRejected as e:
            self.odoo_ok, self.last_error = True, str(e)
            self._audit("error", str(e), self.production_id)
        self.last_poll = self._clock()

    def _reconcile(self) -> None:
        if self.workcenter is None:
            self.workcenter = self.client.find_workcenter(self.cfg.workcenter_code)
            if self.workcenter is None:
                raise OdooRejected(f"no work center with code {self.cfg.workcenter_code!r} in Odoo")
        wos = self.client.open_workorders(self.workcenter["id"])
        mos: dict[int, list[dict]] = {}
        for w in wos:
            mos.setdefault(w["production_id"][0], []).append(w)
        armed = {mid: ws for mid, ws in mos.items()
                 if any(w["production_state"] == "progress" for w in ws)
                 or (self.cfg.arm_on_confirmed and any(w["production_state"] == "confirmed" for w in ws))}
        # All Odoo network calls happen BEFORE the lock is taken: `on_tick` runs on the detection thread and
        # needs this lock for every frame, so a slow or hanging Odoo must never be able to hold it. (Measured
        # on the user's Odoo: ~2 s per call. Holding the lock across those calls paused the camera pipeline
        # every poll, which kept breaking the activity engine's 1.5 s continuity rule.)
        details = None
        if len(armed) == 1:
            mo_id = next(iter(armed))
            if mo_id != self.production_id or self.tracker is None:       # only needed when (re)arming
                details = self.client.workorders_of(mo_id)
        with self._lock:
            if len(armed) > 1:
                names = ", ".join(sorted(ws[0]["production_id"][1] for ws in armed.values()))
                msg = f"more than one MO is in progress at work center {self.cfg.workcenter_code}: {names}"
                if self.conflict != msg:
                    self._audit("conflict", msg)
                self.conflict, self.tracker, self.production_id = msg, None, None
                return
            self.conflict = None
            if not armed:
                if self.tracker is not None and not self._pending_for(self.production_id):
                    self._audit("reconcile", "MO no longer active at this work center - disarmed", self.production_id)
                    self.tracker, self.production_id = None, None
                return
            (mo_id, ws), = armed.items()
            if mo_id != self.production_id or self.tracker is None:
                if details is None:               # state changed since we looked: fetch next poll, no I/O under the lock
                    return
                self._arm(mo_id, ws[0]["production_id"][1], details)
            # Adopt Odoo's real states unless our own queued actions have not been applied yet. In live mode
            # that happens every poll (Odoo is the truth). In shadow mode the bridge's own decisions never
            # reach Odoo, so syncing every poll would erase them - there, adopt only when Odoo's states
            # CHANGED since the last poll, i.e. a person did something in Odoo.
            # `ws` lists this work center's NOT-finished operations of the MO, so one that is missing is done.
            if self.tracker is not None and not self._pending_for(mo_id):
                open_by_id = {w["id"]: w for w in ws}
                states = [_ODOO_TO_STATE.get(open_by_id[i]["state"], PENDING) if i in open_by_id else FINISHED
                          for i in self._wo_ids]
                if self.mode == "live" or states != self._odoo_seen:
                    if self.tracker.sync_states(states):
                        self._audit("reconcile", f"adopted Odoo states {states}", mo_id)
                self._odoo_seen = states

    def _arm(self, mo_id: int, mo_name: str, work_orders: list[dict]) -> None:
        real = [w for w in work_orders if w["state"] != "cancel"]
        mine = [w for w in real if w["workcenter_id"] and w["workcenter_id"][0] == self.workcenter["id"]]
        unmapped = [w["name"] for w in mine if w["name"].strip().lower() not in self.cfg.operation_map]
        if unmapped:
            self.conflict = f"{mo_name}: operation(s) {unmapped} have no camera activity mapped (GARMENT_ODOO_OPERATION_MAP)"
            self._audit("conflict", self.conflict, mo_id)
            self.tracker, self.production_id = None, None
            return
        steps = [Step(w["name"], self.cfg.operation_map[w["name"].strip().lower()]) for w in mine]
        self.tracker = OperationTracker(steps, self.cfg.flow)
        self._wo_ids = [w["id"] for w in mine]
        self._odoo_seen = [_ODOO_TO_STATE.get(w["state"], PENDING) for w in mine]
        self.tracker.sync_states(list(self._odoo_seen))
        self.production_id, self.production_name = mo_id, mo_name
        self._audit("reconcile", f"armed on {mo_name}: " + " -> ".join(f"{s.name}[{s.activity}]" for s in steps), mo_id)

    def _pending_for(self, mo_id: int | None) -> bool:
        if mo_id is None:
            return False
        return bool(self.db.query("SELECT 1 FROM odoo_outbox WHERE production_id=? AND status IN ('pending','failed') LIMIT 1", (mo_id,)))

    # ------------------------------------------------------------------------------- outbox
    def _drain(self) -> None:
        now = self._clock()
        blocked: set[int] = set()
        for row in self.db.query("SELECT * FROM odoo_outbox WHERE status IN ('pending','failed') ORDER BY id"):
            if row["status"] == "failed":
                blocked.add(row["production_id"])          # a failed action holds back that MO's later ones
                continue
            if row["production_id"] in blocked or row["next_try"] > now:
                continue
            try:
                self._apply(row)
            except OdooTransient as e:
                n = row["attempts"] + 1
                self.db.execute("UPDATE odoo_outbox SET attempts=?, next_try=?, last_error=? WHERE id=?",
                                (n, now + BACKOFF_S[min(n - 1, len(BACKOFF_S) - 1)], str(e)[:300], row["id"]))
                raise
            except OdooRejected as e:
                self.db.execute("UPDATE odoo_outbox SET status='failed', attempts=attempts+1, last_error=? WHERE id=?",
                                (str(e)[:300], row["id"]))
                self._audit("error", f"{row['action']} {row['step_name']} rejected by Odoo: {e}", row["production_id"], row["workorder_id"])
                blocked.add(row["production_id"])
                continue
            self.db.execute("UPDATE odoo_outbox SET status='done', done_at=?, last_error=NULL WHERE id=?", (self._clock(), row["id"]))
            self._audit("applied", f"{row['action']} {row['step_name']} applied in Odoo", row["production_id"], row["workorder_id"])

    def _apply(self, row) -> None:
        a, wo, mo = row["action"], row["workorder_id"], row["production_id"]
        if a == "START":
            self.client.start_workorder(wo)
        elif a == "FINISH":
            self.client.finish_workorder(wo)
        elif a == "MO_DONE":
            others = [w for w in self.client.workorders_of(mo) if w["state"] not in ("done", "cancel")]
            if others:                                        # never force other operations closed
                raise OdooRejected(f"MO has unfinished operations elsewhere: {[w['name'] for w in others]}")
            self.client.mark_production_done(mo)
        else:
            raise OdooRejected(f"unknown outbox action {a}")
        if self.cfg.post_notes and a in ("START", "FINISH"):
            try:
                ev = json.loads(row["evidence"] or "{}")
                # work orders are not mail threads in Odoo (verified: message_post does not exist on
                # mrp.workorder), so the audit trail goes on the manufacturing order's chatter
                # plain text: Odoo HTML-escapes the body it is given over the API
                self.client.post_note("mrp.production", mo,
                                      f"Camera bridge: {a.lower()} {row['step_name']} - {row['reason']}" + (f" {ev}" if ev else ""))
            except OdooError:
                pass                                          # the note is a courtesy; the action already succeeded

    # ------------------------------------------------------------------------------ introspection
    def status(self) -> dict:
        with self._lock:
            t = self.tracker.status() if self.tracker else None
            q = self.db.query("SELECT status, COUNT(*) n FROM odoo_outbox WHERE status IN ('pending','failed') GROUP BY status")
            failed = self.db.query("SELECT id, action, step_name, last_error FROM odoo_outbox WHERE status='failed' ORDER BY id")
            return {
                "mode": self.mode, "configured": self.cfg.configured, "workcenter_code": self.cfg.workcenter_code,
                "odoo_ok": self.odoo_ok, "last_error": self.last_error, "last_poll": self.last_poll or None,
                "conflict": self.conflict,
                "camera": {"ticks": self.ticks, "last_tick_age_s": round(self._clock() - self.last_tick_wall, 1) if self.ticks else None,
                           "seeing": self.last_acts},
                "production": {"id": self.production_id, "name": self.production_name} if self.production_id else None,
                "flow": t, "queue": {r["status"]: r["n"] for r in q},
                "failed": [dict(r) for r in failed],
            }

    def audit_log(self, limit: int = 50) -> list[dict]:
        return [dict(r) for r in self.db.query("SELECT * FROM odoo_audit ORDER BY id DESC LIMIT ?", (limit,))]
