"""Production analytics over the unit/visit data (spec §5, §8-10, §15, §17-18, §23).

Definitions (kept explicit because the numbers are only useful if people know what they mean):

  entered      visits that *arrived* at the station in the window (previous station's exit)
  completed    visits that finished processing in the window
  rejected     visits scrapped here in the window
  waiting      units that have arrived but not started        (as of the window end, or now)
  processing   units currently inside the station
  avg processing / avg waiting   over visits that completed / started in the window
  throughput   completed per *active* hour: the span between the first and last event on the
               line in the window (a night with no production doesn't dilute the day), at
               least 15 minutes; rolling windows ("last hour") use their exact length
  yield        finished / (finished + rejected)  - units still in production are neither a
               success nor a loss yet. `started_to_finished` (finished / issued) is reported
               too, since that's the spec's simple definition, but it counts every unit still
               on the floor as a loss.
  cycle time   finished_at - created_at for units that finished in the window

"Cause" is never guessed: bottleneck and delay reports carry the observed numbers
(queue length, throughput in vs out, processing time vs expected) and stop there.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Callable

from . import periods
from .periods import Window
from .store import Store

MIN_ACTIVE_HOURS = 0.25


def _pct(n: float, d: float) -> float | None:
    return None if not d else round(100.0 * n / d, 1)


def _avg(vals: list[float]) -> float | None:
    return sum(vals) / len(vals) if vals else None


class Analytics:
    def __init__(self, store: Store, clock: Callable[[], float] = time.time) -> None:
        self.store = store
        self.clock = clock

    # ------------------------------------------------------------ helpers
    def shifts(self) -> list[dict]:
        return self.store.ensure_factory()["shifts"]

    def window(self, key: str, start=None, end=None) -> Window:
        return periods.resolve(key, self.clock(), self.shifts(), start, end)

    def previous_window(self, w: Window, like_for_like: bool = True) -> Window:
        return periods.previous(w, self.clock(), self.shifts(), like_for_like)

    @staticmethod
    def _uf(alias: str, line_id, product_id, batch_id) -> tuple[str, list]:
        sql, p = "", []
        for col, val in (("line_id", line_id), ("product_id", product_id), ("batch_id", batch_id)):
            if val:
                sql += f" AND {alias}.{col}=?"
                p.append(int(val))
        return sql, p

    def ordered_stations(self, line_id: int, product_id: int | None = None) -> list[dict]:
        """Stations in flow order: a product's own route, or (all products) by earliest step."""
        if product_id:
            r = self.store.q1("SELECT id FROM routes WHERE line_id=? AND product_id=?", (line_id, product_id))
            ids = [s["station_id"] for s in self.store.route_steps(r["id"])] if r else []
            sts = {s["id"]: s for s in self.store.list_stations(line_id)}
            return [sts[i] for i in ids if i in sts]
        rows = self.store.q(
            "SELECT s.id, MIN(rs.seq) pos FROM stations s LEFT JOIN route_steps rs ON rs.station_id=s.id "
            "WHERE s.line_id=? GROUP BY s.id ORDER BY pos IS NULL, pos, s.id", (line_id,))
        by = {s["id"]: s for s in self.store.list_stations(line_id)}
        # the sink has the highest seq of its route; keep sinks last, sources first
        order = sorted(rows, key=lambda r: ({"source": 0, "process": 1, "sink": 2}[by[r["id"]]["kind"]],
                                            r["pos"] if r["pos"] is not None else 99, r["id"]))
        return [by[r["id"]] for r in order]

    def active_hours(self, w: Window, line_id: int | None) -> float:
        """Hours of actual activity in the window (see module docstring)."""
        now = self.clock()
        end = min(w.end, now)
        if w.rolling:
            return max((end - w.start) / 3600.0, MIN_ACTIVE_HOURS)
        row = self.store.q1(
            "SELECT MIN(e.ts) a, MAX(e.ts) b FROM events e JOIN stations s ON s.id=e.station_id "
            "WHERE e.result='applied' AND e.ts>=? AND e.ts<?" + (" AND s.line_id=?" if line_id else ""),
            (w.start, end, *([line_id] if line_id else [])))
        if not row or row["a"] is None:
            return MIN_ACTIVE_HOURS
        return min(max((row["b"] - row["a"]) / 3600.0, MIN_ACTIVE_HOURS), max((end - w.start) / 3600.0, MIN_ACTIVE_HOURS))

    # ------------------------------------------------------------ stations
    def stations(self, w: Window, line_id: int, product_id: int | None = None, batch_id: int | None = None,
                 detail: bool = True) -> list[dict]:
        now = self.clock()
        T = w.asof(now)
        hours = self.active_hours(w, line_id)
        uf, up = self._uf("u", None, product_id, batch_id)
        out = []
        for st in self.ordered_stations(line_id, product_id):
            base = f"FROM visits v JOIN units u ON u.id=v.unit_id WHERE v.station_id=?{uf}"
            bp = [st["id"], *up]
            a, b = w.start, w.end
            agg = self.store.q1(
                "SELECT "
                "SUM(v.arrived_ts>=? AND v.arrived_ts<?) entered, "
                "SUM(v.outcome='completed' AND v.end_ts>=? AND v.end_ts<?) completed, "
                "SUM(v.outcome='rejected' AND v.end_ts>=? AND v.end_ts<?) rejected, "
                "AVG(CASE WHEN v.outcome='completed' AND v.end_ts>=? AND v.end_ts<? THEN v.processing_s END) avg_proc, "
                "COUNT(CASE WHEN v.outcome='completed' AND v.end_ts>=? AND v.end_ts<? THEN v.processing_s END) n_proc, "
                "AVG(CASE WHEN v.start_ts>=? AND v.start_ts<? THEN v.waiting_s END) avg_wait, "
                "COUNT(CASE WHEN v.start_ts>=? AND v.start_ts<? THEN v.waiting_s END) n_wait, "
                "MAX(CASE WHEN v.end_ts<=? THEN v.end_ts END) last_end, "
                "MAX(CASE WHEN v.start_ts<=? THEN v.start_ts END) last_start "
                + base, [a, b] * 7 + [T, T, *bp]) or {}
            waiting = self.store.scalar(
                "SELECT COUNT(*) " + base + " AND v.arrived_ts<=? AND (v.start_ts IS NULL OR v.start_ts>?) "
                "AND (v.end_ts IS NULL OR v.end_ts>?)", [*bp, T, T, T], 0)
            processing = self.store.scalar(
                "SELECT COUNT(*) " + base + " AND v.start_ts<=? AND (v.end_ts IS NULL OR v.end_ts>?)", [*bp, T, T], 0)
            entered, completed, rejected = (int(agg.get("entered") or 0), int(agg.get("completed") or 0),
                                            int(agg.get("rejected") or 0))
            exp_min = st["expected_process_min"]
            avg_proc = agg.get("avg_proc")
            last_act = max([x for x in (agg.get("last_end"), agg.get("last_start")) if x] or [0]) or None
            row = {
                "id": st["id"], "name": st["name"], "kind": st["kind"], "camera_id": st["camera_id"],
                "entered": entered, "completed": completed, "rejected": rejected,
                "waiting": int(waiting), "processing": int(processing),
                "avg_processing_s": avg_proc, "avg_waiting_s": agg.get("avg_wait"),
                "n_processing_samples": int(agg.get("n_proc") or 0), "n_waiting_samples": int(agg.get("n_wait") or 0),
                "throughput_per_h": round(completed / hours, 2) if st["kind"] != "source" or completed else 0.0,
                "rejection_rate_pct": _pct(rejected, completed + rejected),
                "expected_process_min": exp_min, "expected_wait_qty": st["expected_wait_qty"],
                "target_units_per_h": st["target_units_per_h"], "reject_threshold_pct": st["reject_threshold_pct"],
                "idle_alert_min": st["idle_alert_min"], "capacity": st["capacity"],
                "efficiency_pct": round(100.0 * (exp_min * 60.0) / avg_proc, 1) if exp_min and avg_proc else None,
                "last_activity_ts": last_act,
                "idle_min": round((T - last_act) / 60.0, 1) if last_act else None,
            }
            if st["kind"] == "process" and detail:
                row["utilization_pct"] = self._utilization(st["id"], w, T, hours, st["capacity"], uf, up)
                row["peak_waiting"] = self._peak_waiting(st["id"], w, T, uf, up)
            else:
                row["utilization_pct"] = None
                row["peak_waiting"] = row["waiting"]
            out.append(row)
        return out

    def _utilization(self, station_id, w: Window, T: float, hours: float, capacity: int, uf, up) -> float | None:
        """Machine-time in use divided by machine-time available: the time units spent being processed
        in the window / (capacity x active hours). `capacity` is the number of parallel machines or
        positions configured on the station (default 1)."""
        rows = self.store.q(
            "SELECT v.start_ts s, COALESCE(v.end_ts, ?) e FROM visits v JOIN units u ON u.id=v.unit_id "
            f"WHERE v.station_id=? AND v.start_ts IS NOT NULL AND v.start_ts<? AND COALESCE(v.end_ts, ?)>?{uf}",
            [T, station_id, T, T, w.start, *up])
        busy = sum(max(0.0, min(r["e"], T) - max(r["s"], w.start)) for r in rows)
        avail = max(capacity, 1) * hours * 3600.0
        return round(min(100.0, 100.0 * busy / avail), 1) if avail else None

    def _peak_waiting(self, station_id, w: Window, T: float, uf, up) -> int:
        """Largest queue in front of the station during the window (sweep over waiting intervals)."""
        rows = self.store.q(
            "SELECT v.arrived_ts s, COALESCE(v.start_ts, v.end_ts, ?) e FROM visits v JOIN units u ON u.id=v.unit_id "
            f"WHERE v.station_id=? AND v.arrived_ts IS NOT NULL AND v.arrived_ts<? AND COALESCE(v.start_ts, v.end_ts, ?)>?{uf}",
            [T, station_id, T, T, w.start, *up])
        ev = []
        for r in rows:
            s, e = max(r["s"], w.start), min(r["e"], T)
            if e > s:
                ev += [(s, 1), (e, -1)]
        cur = peak = 0
        for _, d in sorted(ev, key=lambda x: (x[0], x[1])):  # departures before arrivals at the same instant
            cur += d
            peak = max(peak, cur)
        return peak

    # ---------------------------------------------------------------- line KPIs
    def kpis(self, w: Window, line_id: int | None = None, product_id: int | None = None,
             batch_id: int | None = None) -> dict:
        now = self.clock()
        T = w.asof(now)
        uf, up = self._uf("u", line_id, product_id, batch_id)
        a, b = w.start, w.end
        c = self.store.q1(
            "SELECT SUM(u.created_at>=? AND u.created_at<?) issued, "
            "SUM(u.finished_at>=? AND u.finished_at<?) finished, "
            "SUM(u.rejected_at>=? AND u.rejected_at<?) rejected "
            f"FROM units u WHERE 1=1{uf}", [a, b, a, b, a, b, *up]) or {}
        issued, finished, rejected = int(c.get("issued") or 0), int(c.get("finished") or 0), int(c.get("rejected") or 0)
        wip = self.store.scalar(
            f"SELECT COUNT(*) FROM units u WHERE u.created_at<=? AND (u.finished_at IS NULL OR u.finished_at>?) "
            f"AND (u.rejected_at IS NULL OR u.rejected_at>?){uf}", [T, T, T, *up], 0)
        cohort = self.store.q1(
            "SELECT COUNT(*) n, SUM(status='finished') fin, SUM(status='rejected') rej, SUM(status='in_progress') wip "
            f"FROM units u WHERE u.created_at>=? AND u.created_at<?{uf}", [a, b, *up]) or {}
        cf, cr = int(cohort.get("fin") or 0), int(cohort.get("rej") or 0)
        fin_rows = self.store.q(
            "SELECT u.id, u.cycle_s, p.target_cycle_min t FROM units u JOIN products p ON p.id=u.product_id "
            f"WHERE u.finished_at>=? AND u.finished_at<?{uf}", [a, b, *up])
        cycles = [r["cycle_s"] for r in fin_rows if r["cycle_s"] is not None]
        with_target = [r for r in fin_rows if r["t"] and r["cycle_s"] is not None]
        avg_cycle = _avg(cycles)
        target = _avg([r["t"] * 60.0 for r in with_target])
        on_time = sum(1 for r in with_target if r["cycle_s"] <= r["t"] * 60.0)
        sums = self.store.q(
            "SELECT SUM(v.processing_s) p, SUM(v.waiting_s) w FROM visits v JOIN units u ON u.id=v.unit_id "
            f"WHERE u.finished_at>=? AND u.finished_at<?{uf} GROUP BY u.id", [a, b, *up])
        hours = self.active_hours(w, line_id)
        return {
            "window": w.to_dict(now),
            "raw_material": issued, "issued": issued, "in_production": int(wip), "finished": finished,
            "rejected": rejected,
            "rejection_rate_pct": _pct(rejected, finished + rejected),
            "yield_pct": _pct(cf, cf + cr), "started_to_finished_pct": _pct(cf, int(cohort.get("n") or 0)),
            "cohort": {"started": int(cohort.get("n") or 0), "finished": cf, "rejected": cr,
                       "in_progress": int(cohort.get("wip") or 0)},
            "avg_cycle_s": avg_cycle,
            # the target only covers finished units of products that have one configured
            "target_cycle_s": target,
            "cycle_diff_s": (avg_cycle - target) if avg_cycle is not None and target is not None else None,
            "on_time_pct": _pct(on_time, len(with_target)),
            "avg_processing_total_s": _avg([r["p"] for r in sums if r["p"] is not None]),
            "avg_waiting_total_s": _avg([r["w"] for r in sums if r["w"] is not None]),
            "throughput_per_h": round(finished / hours, 2),
            "active_hours": round(hours, 2),
        }

    # -------------------------------------------------------------- bottleneck
    def bottleneck(self, line_id: int, product_id: int | None = None, min_arrivals: int = 8) -> dict | None:
        """Where is work piling up right now? Uses the last hour for rates and *now* for queues.

        'bottleneck' = the queue in front of the station exceeds what's expected there (highest
        queue/expected ratio wins). 'potential' = the queue is approaching its limit (>= 60% of it)
        and the station is finishing clearly less per hour than arrives (spec §9: 60/h out vs
        100/h in). Only observations are
        reported - the cause (machine, operator, material...) isn't inferred."""
        now = self.clock()
        stats = [s for s in self.stations(Window(now - 3600, now, "Last hour", "last_hour", True), line_id, product_id)
                 if s["kind"] == "process"]
        best: dict | None = None
        for s in stats:
            arrivals = s["entered"]
            served = s["completed"] + s["rejected"]
            exp = max(s["expected_wait_qty"], 1)
            level, score = None, 0.0
            if s["waiting"] > s["expected_wait_qty"]:
                level, score = "bottleneck", 1000.0 + s["waiting"] / exp
            elif (arrivals >= min_arrivals and served < 0.8 * arrivals
                  and s["waiting"] >= 0.6 * s["expected_wait_qty"] and s["waiting"] > 0):
                # rates alone are noisy (units that just arrived haven't had time to be served),
                # so a rate gap only counts once the queue is also approaching its limit
                level, score = "potential", (arrivals - served) / max(arrivals, 1)
            if level and (best is None or score > best["score"]):
                best = {
                    "score": score, "level": level, "station_id": s["id"], "station": s["name"],
                    "waiting": s["waiting"], "expected_waiting": s["expected_wait_qty"],
                    "throughput_per_h": served, "upstream_throughput_per_h": arrivals,
                    "avg_waiting_s": s["avg_waiting_s"], "avg_processing_s": s["avg_processing_s"],
                }
        if best:
            best.pop("score")
            best["evidence"] = (
                f"{best['waiting']} units waiting (expected up to {best['expected_waiting']}); "
                f"{best['throughput_per_h']} finished vs {best['upstream_throughput_per_h']} arrived in the last hour")
        return best

    # -------------------------------------------------------------- comparison
    def compare(self, a: Window, b: Window, line_id: int | None = None, product_id: int | None = None,
                batch_id: int | None = None, line_b: int | None = None) -> dict:
        """KPI and per-stage changes between two windows (or, with `line_b`, two lines over `a`)."""
        ka = self.kpis(a, line_id, product_id, batch_id)
        kb = self.kpis(b if line_b is None else a, line_b or line_id, product_id, batch_id)
        keys = ["issued", "in_production", "finished", "rejected", "rejection_rate_pct", "yield_pct",
                "avg_cycle_s", "on_time_pct", "throughput_per_h", "avg_processing_total_s", "avg_waiting_total_s"]
        rows = []
        for k in keys:
            va, vb = ka.get(k), kb.get(k)
            d = (va - vb) if va is not None and vb is not None else None
            rows.append({"metric": k, "a": va, "b": vb, "delta": d,
                         "delta_pct": _pct(d, abs(vb)) if d is not None and vb else None})
        st_a = self.stations(a, line_id, product_id, batch_id) if line_id else []
        st_b = self.stations(b if line_b is None else a, line_b or line_id, product_id, batch_id) if (line_b or line_id) else []
        by_b = {s["name"]: s for s in st_b}
        stages = []
        for s in st_a:
            o = by_b.get(s["name"])
            if not o or s["kind"] != "process":
                continue

            def dl(k):
                return (s[k] - o[k]) if s[k] is not None and o[k] is not None else None
            added = sum(x for x in (dl("avg_processing_s"), dl("avg_waiting_s")) if x is not None) if (
                dl("avg_processing_s") is not None or dl("avg_waiting_s") is not None) else None
            stages.append({"station": s["name"], "a": _cmp_fields(s), "b": _cmp_fields(o),
                           "d_avg_processing_s": dl("avg_processing_s"), "d_avg_waiting_s": dl("avg_waiting_s"),
                           "d_peak_waiting": dl("peak_waiting"), "d_completed": dl("completed"),
                           "d_rejection_rate_pct": dl("rejection_rate_pct"), "added_time_per_unit_s": added})
        stages.sort(key=lambda r: -(r["added_time_per_unit_s"] if r["added_time_per_unit_s"] is not None else -1e18))
        return {"a": ka["window"], "b": kb["window"] if line_b is None else ka["window"], "kpis": rows, "stages": stages,
                "a_kpis": ka, "b_kpis": kb}

    # --------------------------------------------------------------- breakdown
    def breakdown(self, by: str, w: Window, line_id: int | None = None, product_id: int | None = None,
                  batch_id: int | None = None, shift: str | None = None) -> list[dict]:
        """Units *started* in the window grouped by day / shift / line / product / batch (§18).
        Each row: started, finished (of those started), rejected, in production, avg cycle, yield."""
        uf, up = self._uf("u", line_id, product_id, batch_id)
        rows = self.store.q(
            "SELECT u.id, u.status, u.created_at, u.cycle_s, u.line_id, u.product_id, u.batch_id, "
            "l.name line_name, p.code product_code, p.name product_name, b.code batch_code "
            "FROM units u JOIN lines l ON l.id=u.line_id JOIN products p ON p.id=u.product_id "
            f"JOIN batches b ON b.id=u.batch_id WHERE u.created_at>=? AND u.created_at<?{uf}",
            [w.start, w.end, *up])
        shifts = self.shifts()
        if by == "shift" or shift:
            for r in rows:
                r["shift"] = periods.shift_of(r["created_at"], shifts)
            if shift:
                rows = [r for r in rows if r["shift"] == shift]
        keyf: dict[str, Callable[[dict], tuple[Any, str]]] = {
            "day": lambda r: (datetime.fromtimestamp(r["created_at"]).strftime("%Y-%m-%d"),) * 2,
            "shift": lambda r: (r["shift"], r["shift"]),
            "line": lambda r: (r["line_id"], r["line_name"]),
            "product": lambda r: (r["product_id"], f"{r['product_code']} - {r['product_name']}"),
            "batch": lambda r: (r["batch_id"], r["batch_code"]),
        }
        if by not in keyf:
            raise ValueError("by must be one of day, shift, line, product, batch")
        groups: dict[Any, dict] = {}
        for r in rows:
            k, label = keyf[by](r)
            g = groups.setdefault(k, {"key": k, "label": label, "started": 0, "finished": 0, "rejected": 0,
                                       "in_production": 0, "_cycles": []})
            g["started"] += 1
            if r["status"] == "finished":
                g["finished"] += 1
                if r["cycle_s"] is not None:
                    g["_cycles"].append(r["cycle_s"])
            elif r["status"] == "rejected":
                g["rejected"] += 1
            else:
                g["in_production"] += 1
        out = []
        for g in groups.values():
            g["avg_cycle_s"] = _avg(g.pop("_cycles"))
            g["yield_pct"] = _pct(g["finished"], g["finished"] + g["rejected"])
            out.append(g)
        out.sort(key=lambda g: str(g["key"]) if by in ("day",) else g["label"])
        if by == "batch":
            out.sort(key=lambda g: g["key"], reverse=True)
        return out

    # --------------------------------------------------------- quality / trends
    def rejection_reasons(self, w: Window, line_id: int | None = None, product_id: int | None = None) -> list[dict]:
        uf, up = self._uf("u", line_id, product_id, None)
        return self.store.q(
            "SELECT s.id station_id, s.name station, COALESCE(v.reject_reason,'other') reason, COUNT(*) n "
            "FROM visits v JOIN units u ON u.id=v.unit_id JOIN stations s ON s.id=v.station_id "
            f"WHERE v.outcome='rejected' AND v.end_ts>=? AND v.end_ts<?{uf} GROUP BY s.id, reason ORDER BY n DESC",
            [w.start, w.end, *up])

    def trend(self, w: Window, line_id: int | None = None, product_id: int | None = None) -> list[dict]:
        """Started / finished / rejected per bucket (hourly up to 3 days, daily beyond) for charts."""
        now = self.clock()
        end = min(w.end, now + 3600 if w.live(now) else w.end)
        step = 3600 if (w.end - w.start) <= 3 * 86400 else 86400
        uf, up = self._uf("u", line_id, product_id, None)
        rows = self.store.q(
            "SELECT u.created_at c, u.finished_at f, u.rejected_at r, u.cycle_s FROM units u "
            f"WHERE ((u.created_at>=? AND u.created_at<?) OR (u.finished_at>=? AND u.finished_at<?) "
            f"OR (u.rejected_at>=? AND u.rejected_at<?)){uf}", [w.start, end] * 3 + up)
        n = int((end - w.start) // step) + 1
        n = max(1, min(n, 24 * 60))
        buckets = [{"start": w.start + i * step, "issued": 0, "finished": 0, "rejected": 0, "_cyc": []} for i in range(n)]

        def idx(ts):
            i = int((ts - w.start) // step)
            return i if 0 <= i < n else None
        for r in rows:
            if r["c"] is not None and (i := idx(r["c"])) is not None and w.start <= r["c"] < end:
                buckets[i]["issued"] += 1
            if r["f"] is not None and (i := idx(r["f"])) is not None and w.start <= r["f"] < end:
                buckets[i]["finished"] += 1
                if r["cycle_s"] is not None:
                    buckets[i]["_cyc"].append(r["cycle_s"])
            if r["r"] is not None and (i := idx(r["r"])) is not None and w.start <= r["r"] < end:
                buckets[i]["rejected"] += 1
        for b in buckets:
            b["avg_cycle_s"] = _avg(b.pop("_cyc"))
        return [{"step_s": step, **b} for b in buckets]

    # -------------------------------------------------------------------- units
    def units(self, line_id=None, product_id=None, batch_id=None, status: str | None = None, q: str | None = None,
              w: Window | None = None, limit: int = 100, offset: int = 0) -> dict:
        uf, up = self._uf("u", line_id, product_id, batch_id)
        where, p = "", []
        if status:
            where += " AND u.status=?"; p.append(status)
        if q:
            where += " AND u.code LIKE ?"; p.append(q.strip().upper() + "%")
        if w:
            where += " AND u.created_at>=? AND u.created_at<?"; p += [w.start, w.end]
        total = self.store.scalar(f"SELECT COUNT(*) FROM units u WHERE 1=1{uf}{where}", [*up, *p], 0)
        rows = self.store.q(
            "SELECT u.id, u.code, u.status, u.created_at, u.finished_at, u.rejected_at, u.cycle_s, u.source, "
            "b.code batch_code, pr.code product_code, l.name line_name, "
            "(SELECT s.name FROM visits v JOIN stations s ON s.id=v.station_id WHERE v.unit_id=u.id "
            " AND v.outcome IN ('waiting','processing') LIMIT 1) AS at_station, "
            "(SELECT v.outcome FROM visits v WHERE v.unit_id=u.id AND v.outcome IN ('waiting','processing') LIMIT 1) AS at_state "
            "FROM units u JOIN batches b ON b.id=u.batch_id JOIN products pr ON pr.id=u.product_id "
            f"JOIN lines l ON l.id=u.line_id WHERE 1=1{uf}{where} ORDER BY u.created_at DESC, u.id DESC LIMIT ? OFFSET ?",
            [*up, *p, limit, offset])
        return {"total": total, "units": rows}

    def journey(self, code: str) -> dict | None:
        u = self.store.q1(
            "SELECT u.*, b.code batch_code, p.code product_code, p.name product_name, p.target_cycle_min, l.name line_name "
            "FROM units u JOIN batches b ON b.id=u.batch_id JOIN products p ON p.id=u.product_id "
            "JOIN lines l ON l.id=u.line_id WHERE u.code=? COLLATE NOCASE", (code.strip(),))
        if not u:
            return None
        visits = self.store.q(
            "SELECT v.*, s.name station, s.kind, s.expected_process_min FROM visits v "
            "JOIN stations s ON s.id=v.station_id WHERE v.unit_id=? ORDER BY v.step_seq", (u["id"],))
        events = self.store.q("SELECT e.ts, e.type, s.name station, e.source, e.result, e.detail FROM events e "
                              "LEFT JOIN stations s ON s.id=e.station_id WHERE e.unit_id=? ORDER BY e.ts, e.id", (u["id"],))
        now = self.clock()
        end = u["finished_at"] or u["rejected_at"] or now
        proc = sum(v["processing_s"] or 0 for v in visits)
        wait = sum(v["waiting_s"] or 0 for v in visits)
        return {"unit": u, "visits": visits, "events": events,
                "totals": {"elapsed_s": end - u["created_at"], "processing_s": proc, "waiting_s": wait,
                           "target_s": u["target_cycle_min"] * 60.0 if u["target_cycle_min"] else None,
                           "skipped_steps": sum(1 for v in visits if v["outcome"] == "skipped")}}

    def batch_flow(self, batch_id: int) -> dict | None:
        """Per-stage counts for one batch (§14): how many reached each stage / are still there."""
        b = self.store.q1("SELECT b.*, p.code product_code, p.name product_name, l.name line_name FROM batches b "
                          "JOIN products p ON p.id=b.product_id JOIN lines l ON l.id=b.line_id WHERE b.id=?", (batch_id,))
        if not b:
            return None
        steps = self.store.route_steps(b["route_id"])
        stages = []
        for s in steps:
            r = self.store.q1(
                "SELECT COUNT(v.arrived_ts) reached, SUM(v.outcome='completed') completed, SUM(v.outcome='rejected') rejected, "
                "SUM(v.outcome='waiting') waiting, SUM(v.outcome='processing') processing, SUM(v.outcome='skipped') skipped, "
                "AVG(v.processing_s) avg_proc, AVG(v.waiting_s) avg_wait "
                "FROM visits v JOIN units u ON u.id=v.unit_id WHERE u.batch_id=? AND v.step_seq=?", (batch_id, s["seq"])) or {}
            stages.append({"seq": s["seq"], "station": s["station_name"], "kind": s["kind"],
                           **{k: (int(r.get(k) or 0)) for k in ("reached", "completed", "rejected", "waiting", "processing", "skipped")},
                           "avg_processing_s": r.get("avg_proc"), "avg_waiting_s": r.get("avg_wait")})
        k = self.kpis(Window(b["created_at"] - 1, time.time() + 1, "Batch", "custom"), b["line_id"], b["product_id"], batch_id)
        return {"batch": b, "stages": stages, "kpis": k}

    # ---------------------------------------------------------------- health
    def data_quality(self, w: Window, line_id: int | None = None) -> dict:
        """How trustworthy is the picture? Counts of events the engine refused or couldn't place."""
        now = self.clock()
        end = min(w.end, now + 1)
        sql = ("SELECT e.result, COUNT(*) n FROM events e LEFT JOIN stations s ON s.id=e.station_id "
               "WHERE e.ts>=? AND e.ts<?" + (" AND s.line_id=?" if line_id else "") + " GROUP BY e.result")
        res = {r["result"]: r["n"] for r in self.store.q(sql, [w.start, end, *([line_id] if line_id else [])])}
        skipped = self.store.scalar(
            "SELECT COUNT(DISTINCT v.unit_id) FROM visits v JOIN units u ON u.id=v.unit_id WHERE v.outcome='skipped' "
            "AND v.end_ts>=? AND v.end_ts<?" + (" AND u.line_id=?" if line_id else ""), [w.start, end, *([line_id] if line_id else [])], 0)
        total = sum(res.values())
        bad = res.get("unmatched", 0) + res.get("error", 0)
        return {"events": total, "applied": res.get("applied", 0), "ignored": res.get("ignored", 0),
                "unmatched": res.get("unmatched", 0), "errors": res.get("error", 0), "units_with_skipped_steps": skipped,
                "problem_pct": _pct(bad, total)}

    def has_simulated_data(self) -> bool:
        return bool(self.store.scalar("SELECT COUNT(*) FROM events WHERE source='simulator'", (), 0))


def _cmp_fields(s: dict) -> dict:
    return {k: s[k] for k in ("entered", "completed", "rejected", "waiting", "avg_processing_s", "avg_waiting_s",
                              "peak_waiting", "rejection_rate_pct", "throughput_per_h")}
