"""Alert engine (spec §16): bottleneck, production delay, machine idle, quality, low production.

`AlertEngine.evaluate()` looks at the current situation and reconciles it with the alerts table:

  * one open alert per (kind, station) - re-evaluating updates its numbers, it never piles up
  * an alert whose condition has cleared is resolved automatically (and if the problem comes
    back later that's a new alert, with its own history)
  * acknowledging an alert keeps it 'ack' while the condition holds, so it doesn't nag
  * every alert carries its evidence (the numbers behind it); no causes are guessed

Thresholds live on the station (expected_process_min, expected_wait_qty, target_units_per_h,
reject_threshold_pct, idle_alert_min); the shared knobs are in `AlertConfig`.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .analytics import Analytics
from .periods import Window
from .store import Store

log = logging.getLogger("mpi.alerts")


@dataclass
class AlertConfig:
    delay_tolerance_pct: float = 15.0   # avg processing may exceed expected by this much
    delay_window_min: int = 120
    delay_min_samples: int = 5
    quality_window_min: int = 240
    quality_min_samples: int = 20       # completed + rejected units before a rate means anything
    low_prod_ratio: float = 0.7         # alert when the last hour is below this share of the target
    low_prod_window_min: int = 60


def _min(s: float | None) -> str:
    return "-" if s is None else f"{s / 60.0:.1f}"


class AlertEngine:
    def __init__(self, store: Store, analytics: Analytics, clock: Callable[[], float] = time.time,
                 cfg: AlertConfig | None = None) -> None:
        self.store, self.analytics, self.clock = store, analytics, clock
        self.cfg = cfg or AlertConfig()

    # ----------------------------------------------------------------- detect
    def detect(self) -> list[dict]:
        """Everything that's wrong right now, as alert dicts (not yet stored)."""
        now = self.clock()
        found: list[dict] = []
        for line in self.store.list_lines():
            try:
                found += self._detect_line(line, now)
            except Exception:  # noqa: BLE001 - one broken line must not silence the others
                log.exception("alert detection failed for line %s", line["name"])
        return found

    def _detect_line(self, line: dict, now: float) -> list[dict]:
        c, a, lid = self.cfg, self.analytics, line["id"]
        hour = a.stations(Window(now - c.low_prod_window_min * 60, now, "hour", "last_hour", True), lid, detail=False)
        delay = {s["id"]: s for s in a.stations(Window(now - c.delay_window_min * 60, now, "delay", "custom", True),
                                                lid, detail=False)}
        qual = {s["id"]: s for s in a.stations(Window(now - c.quality_window_min * 60, now, "quality", "custom", True),
                                               lid, detail=False)}
        line_active = self.store.scalar(
            "SELECT COUNT(*) FROM events e JOIN stations s ON s.id=e.station_id WHERE s.line_id=? "
            "AND e.result='applied' AND e.ts>=?", (lid, now - c.low_prod_window_min * 60), 0) > 0
        out: list[dict] = []

        def add(kind, s, severity, message, evidence):
            out.append({"key": f"{kind}:{s['id']}", "kind": kind, "station_id": s["id"], "line_id": lid,
                        "severity": severity, "message": f"{line['name']} - {s['name']}: {message}", "evidence": evidence})

        for s in hour:
            if s["kind"] != "process":
                continue
            # bottleneck: the queue in front of the station is longer than expected there
            if s["waiting"] > s["expected_wait_qty"]:
                add("bottleneck", s, "high" if s["waiting"] > 2 * max(s["expected_wait_qty"], 1) else "medium",
                    f"{s['waiting']} products are waiting (expected up to {s['expected_wait_qty']}).",
                    {"waiting": s["waiting"], "expected_waiting": s["expected_wait_qty"],
                     "arrived_last_hour": s["entered"], "finished_last_hour": s["completed"] + s["rejected"]})
            # production delay: processing takes longer than expected
            d = delay[s["id"]]
            if (d["expected_process_min"] and d["avg_processing_s"] is not None
                    and d["n_processing_samples"] >= c.delay_min_samples):
                exp_s = d["expected_process_min"] * 60.0
                if d["avg_processing_s"] > exp_s * (1 + c.delay_tolerance_pct / 100.0):
                    var = d["avg_processing_s"] - exp_s
                    add("delay", s, "medium",
                        f"average processing time {_min(d['avg_processing_s'])} min vs expected "
                        f"{d['expected_process_min']:g} min (+{_min(var)} min).",
                        {"avg_processing_s": d["avg_processing_s"], "expected_s": exp_s, "variance_s": var,
                         "samples": d["n_processing_samples"], "window_min": c.delay_window_min})
            # quality: rejection rate above the station's threshold
            q = qual[s["id"]]
            decided = q["completed"] + q["rejected"]
            if decided >= c.quality_min_samples and q["rejection_rate_pct"] is not None \
                    and q["rejection_rate_pct"] > s["reject_threshold_pct"]:
                add("quality", s, "high" if q["rejection_rate_pct"] > 2 * s["reject_threshold_pct"] else "medium",
                    f"rejection rate {q['rejection_rate_pct']:g}% (threshold {s['reject_threshold_pct']:g}%).",
                    {"rejection_rate_pct": q["rejection_rate_pct"], "threshold_pct": s["reject_threshold_pct"],
                     "rejected": q["rejected"], "decided": decided, "window_min": c.quality_window_min})
            # idle: work is waiting, nothing is being processed and nothing has happened here for N minutes
            if s["waiting"] > 0 and s["processing"] == 0:
                oldest = self.store.scalar(
                    "SELECT MIN(arrived_ts) FROM visits WHERE station_id=? AND outcome='waiting'", (s["id"],), None)
                last = self.store.scalar(
                    "SELECT MAX(COALESCE(end_ts, start_ts)) FROM visits WHERE station_id=? AND (end_ts IS NOT NULL OR start_ts IS NOT NULL)",
                    (s["id"],), 0) or 0
                since = max(last, oldest or 0)
                idle_min = (now - since) / 60.0
                if since and idle_min >= s["idle_alert_min"]:
                    add("idle", s, "medium",
                        f"no production activity for {idle_min:.0f} minutes while {s['waiting']} units wait.",
                        {"idle_min": round(idle_min, 1), "waiting": s["waiting"], "threshold_min": s["idle_alert_min"]})
            # low production: below target although work is available
            tgt = s["target_units_per_h"]
            if tgt and line_active:
                demand = s["entered"] + s["waiting"]
                rate = s["completed"] / (c.low_prod_window_min / 60.0)
                if rate < c.low_prod_ratio * tgt and demand >= c.low_prod_ratio * tgt:
                    add("low_production", s, "low",
                        f"{rate:g} units/hour vs target {tgt:g}.",
                        {"actual_per_h": round(rate, 2), "target_per_h": tgt, "waiting": s["waiting"],
                         "arrived_last_hour": s["entered"]})
        return out

    # -------------------------------------------------------------- reconcile
    def evaluate(self) -> dict:
        now = self.clock()
        found = {a["key"]: a for a in self.detect()}
        opened = resolved = 0
        with self.store.tx() as c:
            existing = {r["key"]: r for r in c.execute(
                "SELECT * FROM alerts WHERE status IN ('open','ack')").fetchall()}
            for key, a in found.items():
                row = existing.get(key)
                if row:
                    c.execute("UPDATE alerts SET message=?, evidence=?, severity=?, updated_at=? WHERE id=?",
                              (a["message"], json.dumps(a["evidence"]), a["severity"], now, row["id"]))
                else:
                    c.execute("INSERT INTO alerts(key, kind, station_id, line_id, severity, message, evidence, status, "
                              "opened_at, updated_at) VALUES(?,?,?,?,?,?,?,'open',?,?)",
                              (key, a["kind"], a["station_id"], a["line_id"], a["severity"], a["message"],
                               json.dumps(a["evidence"]), now, now))
                    opened += 1
            for key, row in existing.items():
                if key not in found:
                    c.execute("UPDATE alerts SET status='resolved', resolved_at=?, updated_at=? WHERE id=?",
                              (now, now, row["id"]))
                    resolved += 1
        return {"opened": opened, "resolved": resolved, "active": len(found)}

    def run_forever(self, stop: threading.Event, interval_s: float = 30.0) -> None:
        while not stop.wait(interval_s):
            try:
                self.evaluate()
            except Exception:  # noqa: BLE001
                log.exception("alert evaluation failed")
