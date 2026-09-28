"""Discrete-event model of the reference metal plant, used to create demo data and a live feed.

This is *synthetic*: every event it produces is tagged source='simulator' and the dashboard
shows a "simulated data" banner while any exists. It exists so the product can be shown,
tested and tuned without a factory - not to make the numbers look good.

Model: units arrive as a Poisson process, walk their route (§3/§13), queue FIFO for a free
machine at each station (each station has `capacity` parallel machines), take a lognormal
processing time, and may be rejected when processing ends. Incidents change a station for a
time window: fewer machines, slower processing, higher reject rate.
"""
from __future__ import annotations

import heapq
import math
import random
from dataclasses import dataclass, field

from .blueprint import PROCESS_STATIONS, ROUTES


@dataclass
class StationModel:
    capacity: int
    mean_min: float
    cv: float = 0.22
    reject_p: float = 0.01
    reasons: tuple = ("dimension_issue", "surface_defect")


# processing-time means and machine counts come from the blueprint (one source of truth);
# only the noise and reject behaviour live here
_BEHAVIOUR = {
    "Cutting": (0.22, 0.008, ("dimension_issue", "material_defect")),
    "CNC Machining": (0.22, 0.010, ("dimension_issue", "machine_error")),
    "Drilling": (0.22, 0.012, ("dimension_issue", "machine_error", "operator_error")),
    "Grinding": (0.22, 0.015, ("surface_defect", "dimension_issue")),
    "Quality Inspection": (0.15, 0.018, ("quality_failure", "surface_defect")),
    "Painting": (0.22, 0.010, ("surface_defect", "quality_failure")),
    "Turning": (0.22, 0.010, ("dimension_issue",)),
    "Polishing": (0.22, 0.008, ("surface_defect",)),
    "Bending": (0.22, 0.010, ("dimension_issue",)),
    "Welding": (0.22, 0.014, ("machine_error", "operator_error")),
}
DEFAULT_MODELS = {
    name: StationModel(capacity=PROCESS_STATIONS[name][2], mean_min=PROCESS_STATIONS[name][0],
                       cv=cv, reject_p=rp, reasons=reasons)
    for name, (cv, rp, reasons) in _BEHAVIOUR.items()
}


@dataclass
class Incident:
    station: str
    start: float
    end: float
    machines: int | None = None      # machines available during the incident (None = unchanged)
    slow: float = 1.0                # processing-time multiplier
    reject_p: float | None = None    # reject probability override
    note: str = ""


@dataclass
class SimEvent:
    ts: float
    type: str
    station: str
    code: str
    product: str | None = None
    batch: str | None = None
    reason: str | None = None


@dataclass
class LineModel:
    name: str
    rate_per_h: float = 24.0
    mix: dict = field(default_factory=lambda: {"GEAR": 0.7, "SHAFT": 0.15, "BRACKET": 0.15})
    slow: dict = field(default_factory=dict)        # station -> permanent multiplier (a slower line)
    models: dict = field(default_factory=lambda: {k: StationModel(**vars(v)) for k, v in DEFAULT_MODELS.items()})


def _lognormal(rng: random.Random, mean: float, cv: float) -> float:
    sigma = math.sqrt(math.log(1 + cv * cv))
    mu = math.log(mean) - sigma * sigma / 2
    return rng.lognormvariate(mu, sigma)


def simulate(line: LineModel, arrivals_from: float, arrivals_to: float, rng: random.Random,
             codes: dict[str, int], batch_of: dict[str, str], incidents: list[Incident] = (),
             rate_factor: float = 1.0, transit_min: tuple = (0.5, 2.5)) -> list[SimEvent]:
    """Events for every unit that *arrives* in [arrivals_from, arrivals_to). `codes` maps product code ->
    last used number (updated), `batch_of` maps product code -> batch code."""
    rate = line.rate_per_h * rate_factor / 3600.0
    events: list[SimEvent] = []
    # machine free-times per station
    free: dict[str, list[float]] = {n: [0.0] * m.capacity for n, m in line.models.items()}
    inc_by: dict[str, list[Incident]] = {}
    for i in incidents:
        inc_by.setdefault(i.station, []).append(i)

    prods = list(line.mix)
    weights = [line.mix[p] for p in prods]
    heap: list[tuple[float, int, str, str, int]] = []   # (arrive_ts, tiebreak, code, product, stage_idx)
    tie = 0
    t = arrivals_from
    while True:
        t += rng.expovariate(rate)
        if t >= arrivals_to:
            break
        p = rng.choices(prods, weights)[0]
        codes[p] = codes.get(p, 0) + 1
        code = f"{p}-{codes[p]:06d}"
        events.append(SimEvent(t, "issue", "Raw Material", code, p, batch_of.get(p)))
        tie += 1
        heapq.heappush(heap, (t + rng.uniform(*transit_min) * 60, tie, code, p, 1))

    while heap:
        arr, _, code, prod, idx = heapq.heappop(heap)
        route = ROUTES[prod]
        name = route[idx]
        if idx == len(route) - 1:          # sink: finished automatically on the previous exit
            continue
        m = line.models[name]
        incs = [i for i in inc_by.get(name, []) if i.start <= arr < i.end]
        avail = m.capacity
        for i in incs:
            if i.machines is not None:
                avail = min(avail, i.machines)
        machines = free[name]
        # a machine outside the incident's available set can't start until the incident ends
        ends = max((i.end for i in incs if i.machines is not None), default=0.0)
        best_k, best_start = 0, float("inf")
        for k, f in enumerate(machines):
            st = max(arr, f) if k < avail else max(arr, f, ends)
            if st < best_start:
                best_k, best_start = k, st
        start = best_start
        slow = line.slow.get(name, 1.0)
        for i in incs:
            slow *= i.slow
        # an incident also covers processing that starts inside its window
        for i in inc_by.get(name, []):
            if i not in incs and i.start <= start < i.end:
                slow *= i.slow
        dur = _lognormal(rng, m.mean_min * slow, m.cv) * 60
        end = start + dur
        machines[best_k] = end
        rp = m.reject_p
        for i in inc_by.get(name, []):
            if i.start <= end < i.end and i.reject_p is not None:
                rp = i.reject_p
        events.append(SimEvent(start, "enter", name, code, prod))
        if rng.random() < rp:
            reason = rng.choice(m.reasons)
            events.append(SimEvent(end, "reject", name, code, prod, reason=reason))
            continue
        events.append(SimEvent(end, "exit", name, code, prod))
        tie += 1
        heapq.heappush(heap, (end + rng.uniform(*transit_min) * 60, tie, code, prod, idx + 1))
    events.sort(key=lambda e: e.ts)
    return events
