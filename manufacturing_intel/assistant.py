"""Natural-language questions about production (spec §19-20).

Deliberately *not* an LLM: a deterministic router that maps a question to the analytics
functions and phrases what they return. Consequences worth knowing:

  * every number in an answer comes from the database, and the answer says which line and
    period it assumed - so a wrong guess about what was meant is visible, not hidden
  * "why" questions get observed changes (queue length, processing/waiting time, rejects) ranked
    by impact, never an invented cause - the spec is explicit about that (§9, §19)
  * questions it can't place get a list of what it can answer, rather than a plausible-sounding guess

An LLM can later sit in front of the same analytics functions; the evidence blocks are already
structured for that.
"""
from __future__ import annotations

import re
from typing import Any

from .analytics import Analytics
from .periods import PeriodError, Window

PERIOD_WORDS = [  # (regex, period key) - first match wins, longest phrases first
    (r"previous shift|last shift", "previous_shift"), (r"current shift|this shift", "current_shift"),
    (r"last hour|past hour", "last_hour"), (r"last 24 hours|past 24 hours", "last_24h"),
    (r"last 7 days|past 7 days|past week", "last_7d"), (r"last 30 days|past 30 days", "last_30d"),
    (r"last week", "last_week"), (r"this week", "this_week"),
    (r"last month", "last_month"), (r"this month", "this_month"),
    (r"yesterday", "yesterday"), (r"today|now|currently|current|right now", "today"),
]
ALIASES = {"cnc": "CNC Machining", "qc": "Quality Inspection", "quality": "Quality Inspection",
           "inspection": "Quality Inspection", "raw": "Raw Material", "finished goods": "Finished Goods"}

EXAMPLES = [
    "How many products were completed today?", "What is the current production quantity?",
    "How many products moved from CNC to Drilling?", "What is the average production time today?",
    "Which stage takes the longest?", "What is the average waiting time between CNC and Drilling?",
    "Where is the current bottleneck?", "Which stage had the highest waiting time this week?",
    "Why is today's production lower than yesterday?", "Which stage has the highest rejection rate?",
    "How many products were rejected today?", "Compare today's production with yesterday.",
    "Compare Line A and Line B.", "Compare this month's average production time with last month.",
    "Where is GEAR-000125?",
]


def dur(s: float | None) -> str:
    if s is None:
        return "n/a"
    s = abs(s)
    if s >= 3600:
        return f"{int(s // 3600)}h {int(round((s % 3600) / 60)):02d}m"
    return f"{s / 60:.1f} min"


def signed_dur(s: float | None) -> str:
    return "n/a" if s is None else ("+" if s >= 0 else "-") + dur(s)


def pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v:g}%"


class Assistant:
    def __init__(self, analytics: Analytics) -> None:
        self.a = analytics
        self.store = analytics.store

    # ------------------------------------------------------------------ entry
    def ask(self, question: str, line_id: int | None = None) -> dict:
        q = (question or "").strip()
        if not q:
            return self._reply("help", "Ask me something about production, for example one of these.", suggestions=True)
        if len(q) > 500:
            return self._reply("help", "That's too long for me - keep the question under 500 characters.", suggestions=True)
        low = re.sub(r"[?!.,;]", " ", q.lower())
        low = re.sub(r"'s\b", "", low)
        ctx = self._context(low, q, line_id)
        try:
            for rule in (self._unit, self._compare_lines, self._compare_periods, self._why, self._bottleneck,
                         self._moved, self._waiting_between, self._rankings, self._rejected, self._cycle, self._yield,
                         self._wip, self._throughput, self._finished):
                out = rule(low, ctx)
                if out:
                    return out
        except PeriodError as e:
            return self._reply("error", str(e))
        return self._reply("help", "I couldn't match that to something I can answer from the production data. "
                                   "Try one of these:", suggestions=True)

    # ---------------------------------------------------------------- context
    def _context(self, low: str, raw: str, line_id: int | None) -> dict:
        lines = self.store.list_lines()
        mentioned = [l for l in lines if l["name"].lower() in low]
        mentioned.sort(key=lambda l: low.index(l["name"].lower()))
        line = mentioned[0] if mentioned else (next((l for l in lines if l["id"] == line_id), None) or (lines[0] if lines else None))
        periods_found = []
        rest = low
        for rx, key in PERIOD_WORDS:
            m = re.search(rf"\b(?:{rx})\b", rest)
            if m:
                periods_found.append((m.start(), key))
                rest = rest[:m.start()] + " " * (m.end() - m.start()) + rest[m.end():]
        periods_found.sort()
        keys = [k for _, k in periods_found]
        products = [p for p in self.store.list_products() if p["code"].lower() in low or p["name"].lower() in low]
        stations = []
        if line:
            sts = self.store.list_stations(line["id"])
            hits = []
            for st in sts:
                nm = st["name"].lower()
                pos = low.find(nm)
                if pos < 0:
                    for w in nm.split():
                        if len(w) > 3 and re.search(rf"\b{re.escape(w)}", low):
                            pos = low.find(w)
                            break
                if pos >= 0:
                    hits.append((pos, st))
            for alias, target in ALIASES.items():
                m = re.search(rf"\b{re.escape(alias)}\b", low)
                if m:
                    st = next((s for s in sts if s["name"] == target), None)
                    if st and all(st["id"] != h[1]["id"] for h in hits):
                        hits.append((m.start(), st))
            stations = [s for _, s in sorted(hits, key=lambda x: x[0])]
        return {"line": line, "lines": mentioned, "period_keys": keys, "products": products, "stations": stations,
                "line_assumed": not mentioned, "raw": raw}

    def _w(self, ctx: dict, default: str = "today", idx: int = 0) -> tuple[Window, bool]:
        keys = ctx["period_keys"]
        key = keys[idx] if len(keys) > idx else default
        return self.a.window(key), len(keys) <= idx

    def _scope(self, ctx: dict, w: Window, assumed_period: bool) -> str:
        bits = []
        if ctx["line"]:
            bits.append(ctx["line"]["name"] + (" (assumed)" if ctx["line_assumed"] and len(self.store.list_lines()) > 1 else ""))
        bits.append(w.label + (" (assumed)" if assumed_period else ""))
        if ctx["products"]:
            bits.append(ctx["products"][0]["name"])
        return " · ".join(bits)

    def _reply(self, intent: str, text: str, evidence: list | None = None, scope: str | None = None,
               table: dict | None = None, suggestions: bool = False) -> dict:
        return {"intent": intent, "answer": text, "evidence": evidence or [], "table": table, "scope": scope,
                "suggestions": EXAMPLES[:8] if suggestions else []}

    def _pair(self, keys: list[str]):
        """(this period, period to compare with). A live period is compared with its natural predecessor
        cut at the same elapsed time - "today so far" against all of yesterday would overstate any gap -
        whether the question named the predecessor ("...than yesterday") or not."""
        a = self.a.window(keys[0] if keys else "today")
        if len(keys) < 2:
            return a, self.a.previous_window(a)
        b = self.a.window(keys[1])
        natural = self.a.previous_window(a)
        return (a, natural) if natural.start == b.start else (a, b)

    def _f(self, ctx):
        return dict(line_id=ctx["line"]["id"] if ctx["line"] else None,
                    product_id=ctx["products"][0]["id"] if ctx["products"] else None)

    # ----------------------------------------------------------------- rules
    def _unit(self, low, ctx):
        m = re.search(r"\b([a-z][a-z0-9_]*-\d{2,})\b", low)
        if not m:
            return None
        j = self.a.journey(m.group(1))
        if not j:
            return self._reply("unit", f"I don't have a unit with code {m.group(1).upper()}.")
        u, t = j["unit"], j["totals"]
        cur = next((v for v in j["visits"] if v["outcome"] in ("waiting", "processing")), None)
        if u["status"] == "finished":
            text = f"{u['code']} finished after {dur(t['elapsed_s'])} ({dur(t['processing_s'])} processing, {dur(t['waiting_s'])} waiting)."
        elif u["status"] == "rejected":
            rj = next((v for v in j["visits"] if v["outcome"] == "rejected"), None)
            text = f"{u['code']} was rejected at {rj['station'] if rj else '?'}" + (f" ({rj['reject_reason'].replace('_', ' ')})" if rj and rj["reject_reason"] else "") + "."
        else:
            text = f"{u['code']} is {cur['outcome'] if cur else 'in production'} at {cur['station'] if cur else '?'}; {dur(t['elapsed_s'])} since it entered the line."
        ev = [[v["station"], f"{v['outcome']}" + (f" · proc {dur(v['processing_s'])}" if v["processing_s"] else "")
               + (f" · wait {dur(v['waiting_s'])}" if v["waiting_s"] else "")] for v in j["visits"]]
        return self._reply("unit", text, ev, f"{u['line_name']} · batch {u['batch_code']}")

    def _compare_lines(self, low, ctx):
        if "compare" not in low or len(ctx["lines"]) < 2:
            return None
        la, lb = ctx["lines"][0], ctx["lines"][1]
        w, assumed = self._w(ctx)
        c = self.a.compare(w, w, la["id"], ctx["products"][0]["id"] if ctx["products"] else None, line_b=lb["id"])
        rows = {r["metric"]: r for r in c["kpis"]}
        ev = [[la["name"] + " finished", rows["finished"]["a"]], [lb["name"] + " finished", rows["finished"]["b"]],
              [la["name"] + " avg production time", dur(rows["avg_cycle_s"]["a"])], [lb["name"] + " avg production time", dur(rows["avg_cycle_s"]["b"])],
              [la["name"] + " yield", pct(rows["yield_pct"]["a"])], [lb["name"] + " yield", pct(rows["yield_pct"]["b"])]]
        text = (f"{la['name']} finished {rows['finished']['a']} vs {lb['name']} {rows['finished']['b']}; average production time "
                f"{dur(rows['avg_cycle_s']['a'])} vs {dur(rows['avg_cycle_s']['b'])}; yield {pct(rows['yield_pct']['a'])} vs {pct(rows['yield_pct']['b'])}.")
        return self._reply("compare_lines", text, ev, f"{la['name']} vs {lb['name']} · {w.label}", self._stage_table(c, la["name"], lb["name"]))

    def _compare_periods(self, low, ctx):
        if "compare" not in low or not ctx["line"]:
            return None
        a, b = self._pair(ctx["period_keys"])
        f = self._f(ctx)
        c = self.a.compare(a, b, f["line_id"], f["product_id"])
        rows = {r["metric"]: r for r in c["kpis"]}
        ev = [[f"finished ({a.label})", rows["finished"]["a"]], [f"finished ({b.label})", rows["finished"]["b"]],
              [f"avg production time ({a.label})", dur(rows["avg_cycle_s"]["a"])], [f"avg production time ({b.label})", dur(rows["avg_cycle_s"]["b"])],
              [f"yield ({a.label})", pct(rows["yield_pct"]["a"])], [f"yield ({b.label})", pct(rows["yield_pct"]["b"])]]
        d = rows["finished"]["delta"]
        text = (f"{a.label}: {rows['finished']['a']} finished vs {rows['finished']['b']} for {b.label} "
                f"({'+' if (d or 0) >= 0 else ''}{d}); average production time {dur(rows['avg_cycle_s']['a'])} vs {dur(rows['avg_cycle_s']['b'])}.")
        return self._reply("compare_periods", text, ev, f"{ctx['line']['name']} · {a.label} vs {b.label}", self._stage_table(c, a.label, b.label))

    @staticmethod
    def _stage_table(c: dict, a_label: str, b_label: str) -> dict | None:
        rows = [[s["station"], dur(s["a"]["avg_processing_s"]), dur(s["b"]["avg_processing_s"]), dur(s["a"]["avg_waiting_s"]),
                 dur(s["b"]["avg_waiting_s"]), s["a"]["peak_waiting"], s["b"]["peak_waiting"]] for s in c["stages"]]
        if not rows:
            return None
        return {"columns": ["Stage", f"Proc ({a_label})", f"Proc ({b_label})", f"Wait ({a_label})", f"Wait ({b_label})",
                            f"Peak queue ({a_label})", f"Peak queue ({b_label})"], "rows": rows}

    def _why(self, low, ctx):
        if not re.search(r"\bwhy\b", low) or not re.search(r"slow|lower|less|drop|delay|decreas|down|behind|fell|worse|reduc", low):
            return None
        if not ctx["line"]:
            return self._reply("why", "There is no production line configured yet.")
        a, b = self._pair(ctx["period_keys"])
        f = self._f(ctx)
        c = self.a.compare(a, b, f["line_id"], f["product_id"])
        rows = {r["metric"]: r for r in c["kpis"]}
        fa, fb = rows["finished"]["a"], rows["finished"]["b"]
        head = f"{a.label} finished production: {fa} units. {b.label}: {fb} units. Difference: {fa - fb:+d} units."
        movers = [s for s in c["stages"] if s["added_time_per_unit_s"] is not None and s["added_time_per_unit_s"] > 60]
        ev = [["Finished", f"{fa} vs {fb}"], ["Avg production time", f"{dur(rows['avg_cycle_s']['a'])} vs {dur(rows['avg_cycle_s']['b'])}"],
              ["Rejected", f"{rows['rejected']['a']} vs {rows['rejected']['b']}"], ["Started", f"{rows['issued']['a']} vs {rows['issued']['b']}"]]
        parts = [head]
        if rows["issued"]["delta"] is not None and rows["issued"]["delta"] < 0 and (fa - fb) < 0:
            parts.append(f"Fewer units were started ({rows['issued']['a']} vs {rows['issued']['b']}), which alone explains part of the gap.")
        if movers:
            top = movers[0]
            parts.append(f"The largest observed change was at {top['station']}: average processing "
                         f"{dur(top['b']['avg_processing_s'])} -> {dur(top['a']['avg_processing_s'])}, average waiting "
                         f"{dur(top['b']['avg_waiting_s'])} -> {dur(top['a']['avg_waiting_s'])}, peak queue "
                         f"{top['b']['peak_waiting']} -> {top['a']['peak_waiting']}.")
            if len(movers) > 1:
                parts.append("Also up: " + "; ".join(f"{m['station']} ({signed_dur(m['added_time_per_unit_s'])} per unit)" for m in movers[1:4]) + ".")
        else:
            parts.append("No stage shows a meaningfully longer processing or waiting time than in the comparison period.")
        rej = [s for s in c["stages"] if s["d_rejection_rate_pct"] is not None and s["d_rejection_rate_pct"] >= 2]
        if rej:
            parts.append("Rejection rate rose at " + ", ".join(f"{s['station']} (+{s['d_rejection_rate_pct']:.1f} pts)" for s in rej[:3]) + ".")
        parts.append("This describes what changed in the records; the operational cause (machine, operator, material...) "
                     "has to be checked against machine and operator logs.")
        return self._reply("why", " ".join(parts), ev, f"{ctx['line']['name']} · {a.label} vs {b.label}", self._stage_table(c, a.label, b.label))

    def _bottleneck(self, low, ctx):
        if "bottleneck" not in low and not re.search(r"highest (waiting|wait)|longest wait|most waiting", low):
            return None
        if not ctx["line"]:
            return self._reply("bottleneck", "There is no production line configured yet.")
        f = self._f(ctx)
        w, assumed = self._w(ctx)
        live = w.key in ("today", "current_shift", "last_hour") and not ctx["period_keys"] or "current" in low or "now" in low
        if "bottleneck" in low and live:
            b = self.a.bottleneck(f["line_id"], f["product_id"])
            scope = f"{ctx['line']['name']} · right now (last hour rates)"
            if not b:
                return self._reply("bottleneck", "No bottleneck right now: no station has a queue above its expected level, and none is "
                                                 "finishing clearly less than arrives.", [], scope)
            kind = "Bottleneck" if b["level"] == "bottleneck" else "Potential bottleneck"
            ev = [["Stage", b["station"]], ["Products waiting", b["waiting"]], ["Expected waiting", b["expected_waiting"]],
                  ["Current throughput", f"{b['throughput_per_h']}/hour"], ["Upstream throughput", f"{b['upstream_throughput_per_h']}/hour"]]
            return self._reply("bottleneck", f"{kind}: {b['station']}. {b['evidence']}. The data shows where work is piling up, not why.", ev, scope)
        stats = [s for s in self.a.stations(w, f["line_id"], f["product_id"]) if s["kind"] == "process" and s["avg_waiting_s"] is not None]
        if not stats:
            return self._reply("bottleneck", f"There is no waiting-time data for {w.label}.", [], self._scope(ctx, w, assumed))
        stats.sort(key=lambda s: -s["avg_waiting_s"])
        top = stats[0]
        ev = [[s["name"], f"avg wait {dur(s['avg_waiting_s'])} · peak queue {s['peak_waiting']}"] for s in stats[:6]]
        return self._reply("bottleneck", f"{top['name']} had the highest average waiting time in {w.label}: {dur(top['avg_waiting_s'])} "
                                         f"(peak queue {top['peak_waiting']}).", ev, self._scope(ctx, w, assumed))

    def _moved(self, low, ctx):
        if not re.search(r"moved|move|went|passed|transferred|from .+ to ", low) or len(ctx["stations"]) < 2 or "waiting" in low:
            return None
        x, y = ctx["stations"][0], ctx["stations"][1]
        w, assumed = self._w(ctx)
        n = self.store.scalar(
            "SELECT COUNT(DISTINCT vx.unit_id) FROM visits vx JOIN visits vy ON vy.unit_id=vx.unit_id AND vy.step_seq>vx.step_seq "
            "JOIN units u ON u.id=vx.unit_id WHERE vx.station_id=? AND vy.station_id=? AND vx.outcome='completed' "
            "AND vx.end_ts>=? AND vx.end_ts<? AND vy.arrived_ts IS NOT NULL" + (" AND u.product_id=?" if ctx["products"] else ""),
            [x["id"], y["id"], w.start, w.end, *([ctx["products"][0]["id"]] if ctx["products"] else [])], 0)
        return self._reply("moved", f"{n} products moved from {x['name']} to {y['name']} in {w.label}.",
                           [["From", x["name"]], ["To", y["name"]], ["Units", n]], self._scope(ctx, w, assumed))

    def _waiting_between(self, low, ctx):
        if "waiting" not in low and "wait time" not in low or not re.search(r"between|average wait|avg wait|waiting time", low):
            return None
        if not ctx["line"] or not ctx["stations"]:
            return None
        w, assumed = self._w(ctx)
        f = self._f(ctx)
        sts = {s["id"]: s for s in self.a.stations(w, f["line_id"], f["product_id"])}
        target = ctx["stations"][-1]
        s = sts.get(target["id"])
        if not s or s["avg_waiting_s"] is None:
            return self._reply("waiting", f"No waiting-time data for {target['name']} in {w.label}.", [], self._scope(ctx, w, assumed))
        via = f"between {ctx['stations'][0]['name']} and {target['name']}" if len(ctx["stations"]) > 1 else f"before {target['name']}"
        return self._reply("waiting", f"The average waiting time {via} in {w.label} was {dur(s['avg_waiting_s'])} "
                                      f"({s['n_waiting_samples']} units; peak queue {s['peak_waiting']}). "
                                      "It's measured from the exit of the previous station to the start at " + target["name"] + ".",
                           [["Average waiting", dur(s["avg_waiting_s"])], ["Samples", s["n_waiting_samples"]], ["Peak queue", s["peak_waiting"]]],
                           self._scope(ctx, w, assumed))

    def _rankings(self, low, ctx):
        kind = None
        if re.search(r"(takes|take|taking) the longest|slowest|longest (stage|processing)|which stage.*longest", low):
            kind = "proc"
        elif re.search(r"highest (rejection|reject)|most reject|worst quality|highest scrap", low):
            kind = "rej"
        if not kind or not ctx["line"]:
            return None
        w, assumed = self._w(ctx)
        f = self._f(ctx)
        stats = [s for s in self.a.stations(w, f["line_id"], f["product_id"]) if s["kind"] == "process"]
        if kind == "proc":
            stats = [s for s in stats if s["avg_processing_s"] is not None]
            stats.sort(key=lambda s: -s["avg_processing_s"])
            if not stats:
                return self._reply("ranking", f"No completed processing in {w.label}.", [], self._scope(ctx, w, assumed))
            ev = [[s["name"], dur(s["avg_processing_s"]) + (f" (expected {s['expected_process_min']:g} min)" if s["expected_process_min"] else "")] for s in stats]
            return self._reply("ranking", f"{stats[0]['name']} takes the longest: {dur(stats[0]['avg_processing_s'])} on average in {w.label}.",
                               ev, self._scope(ctx, w, assumed))
        stats = [s for s in stats if s["rejection_rate_pct"] is not None and s["completed"] + s["rejected"] >= 5]
        stats.sort(key=lambda s: -s["rejection_rate_pct"])
        if not stats:
            return self._reply("ranking", f"Not enough finished units in {w.label} to rank rejection rates.", [], self._scope(ctx, w, assumed))
        ev = [[s["name"], f"{pct(s['rejection_rate_pct'])} ({s['rejected']} of {s['completed'] + s['rejected']})"] for s in stats]
        return self._reply("ranking", f"{stats[0]['name']} has the highest rejection rate in {w.label}: {pct(stats[0]['rejection_rate_pct'])} "
                                      f"({stats[0]['rejected']} rejected).", ev, self._scope(ctx, w, assumed))

    def _rejected(self, low, ctx):
        if not re.search(r"reject|scrap|defect", low) or not ctx["line"]:
            return None
        w, assumed = self._w(ctx)
        f = self._f(ctx)
        k = self.a.kpis(w, **f)
        reasons = self.a.rejection_reasons(w, **f)
        ev = [[f"{r['station']} - {r['reason'].replace('_', ' ')}", r["n"]] for r in reasons[:8]]
        return self._reply("rejected", f"{k['rejected']} products were rejected in {w.label} (rejection rate {pct(k['rejection_rate_pct'])}).",
                           ev, self._scope(ctx, w, assumed))

    def _cycle(self, low, ctx):
        if not re.search(r"production time|cycle time|how long does it take|lead time", low) or not ctx["line"]:
            return None
        w, assumed = self._w(ctx)
        k = self.a.kpis(w, **self._f(ctx))
        if k["avg_cycle_s"] is None:
            return self._reply("cycle", f"No units finished in {w.label}.", [], self._scope(ctx, w, assumed))
        text = f"The average production time in {w.label} was {dur(k['avg_cycle_s'])}"
        if k["target_cycle_s"]:
            text += f" against a target of {dur(k['target_cycle_s'])} ({signed_dur(k['cycle_diff_s'])})"
        text += f", over {k['finished']} finished units."
        ev = [["Average production time", dur(k["avg_cycle_s"])], ["Target", dur(k["target_cycle_s"])], ["Average processing (per unit)", dur(k["avg_processing_total_s"])],
              ["Average waiting (per unit)", dur(k["avg_waiting_total_s"])], ["On time", pct(k["on_time_pct"])]]
        return self._reply("cycle", text, ev, self._scope(ctx, w, assumed))

    def _yield(self, low, ctx):
        if "yield" not in low or not ctx["line"]:
            return None
        w, assumed = self._w(ctx)
        k = self.a.kpis(w, **self._f(ctx))
        c = k["cohort"]
        return self._reply("yield", f"Production yield for {w.label}: {pct(k['yield_pct'])} of units that are finished or rejected "
                                    f"({c['finished']} finished, {c['rejected']} rejected); {c['in_progress']} of the {c['started']} started are still in production. "
                                    f"Counting those as losses would give {pct(k['started_to_finished_pct'])}.",
                           [["Started", c["started"]], ["Finished", c["finished"]], ["Rejected", c["rejected"]], ["In production", c["in_progress"]]],
                           self._scope(ctx, w, assumed))

    def _wip(self, low, ctx):
        if not re.search(r"current production|in production|work in progress|wip|on the floor|in process", low) or not ctx["line"]:
            return None
        w = self.a.window("today")
        f = self._f(ctx)
        k = self.a.kpis(w, **f)
        stats = [s for s in self.a.stations(w, f["line_id"], f["product_id"]) if s["kind"] == "process" and (s["waiting"] or s["processing"])]
        ev = [[s["name"], f"{s['processing']} processing · {s['waiting']} waiting"] for s in stats]
        return self._reply("wip", f"{k['in_production']} products are in production right now.", ev, f"{ctx['line']['name']} · now")

    def _throughput(self, low, ctx):
        if not re.search(r"throughput|per hour|units/hour|rate", low) or not ctx["line"]:
            return None
        w, assumed = self._w(ctx)
        f = self._f(ctx)
        k = self.a.kpis(w, **f)
        return self._reply("throughput", f"Throughput in {w.label}: {k['throughput_per_h']} finished units per active hour "
                                         f"({k['finished']} finished over {k['active_hours']} hours of activity).",
                           [["Finished", k["finished"]], ["Active hours", k["active_hours"]]], self._scope(ctx, w, assumed))

    def _finished(self, low, ctx):
        if not re.search(r"how many|number of|total|count", low) or not re.search(
                r"complet|finish|produc|made|output|start|issued|raw", low) or not ctx["line"]:
            return None
        w, assumed = self._w(ctx)
        k = self.a.kpis(w, **self._f(ctx))
        if re.search(r"start|issued|raw", low):
            return self._reply("issued", f"{k['issued']} products were started (raw material issued) in {w.label}.",
                               [["Started", k["issued"]]], self._scope(ctx, w, assumed))
        return self._reply("finished", f"{k['finished']} products were completed in {w.label}"
                                       f" ({k['rejected']} rejected, {k['in_production']} still in production).",
                           [["Finished", k["finished"]], ["Started", k["issued"]], ["Rejected", k["rejected"]], ["In production", k["in_production"]]],
                           self._scope(ctx, w, assumed))
