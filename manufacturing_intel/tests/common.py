"""Shared test fixtures: an in-memory store with the reference factory and a fake clock."""
from __future__ import annotations

from manufacturing_intel.blueprint import FINISHED, RAW, build_metal_factory
from manufacturing_intel.engine import ProductionEngine
from manufacturing_intel.store import Store

T0 = 1_790_000_000.0  # an arbitrary "09:00" - tests use offsets from it


def M(minutes: float) -> float:
    """minutes -> seconds offset"""
    return minutes * 60.0


class Fx:
    """store + engine + ids for Line A, all three products, and a controllable clock."""

    def __init__(self, lines=("Line A",), products=("GEAR", "SHAFT", "BRACKET"), now: float = T0 + 86400.0,
                 t0: float = T0):
        self.now = now
        self.t0 = t0
        self.store = Store(":memory:")
        self.ids = build_metal_factory(self.store, lines=lines, products=products)
        self.engine = ProductionEngine(self.store, clock=lambda: self.now)
        self.line = self.ids["lines"]["Line A"]
        self.st = self.ids["stations"]["Line A"]

    def ev(self, type, station, minute, code=None, **kw):
        return self.engine.record(type, self.st[station], ts=self.t0 + M(minute), unit_code=code, **kw)

    def unit(self, code):
        return self.store.q1("SELECT * FROM units WHERE code=?", (code,))

    def visits(self, code):
        return self.store.q("SELECT v.*, s.name AS station FROM visits v JOIN stations s ON s.id=v.station_id "
                            "JOIN units u ON u.id=v.unit_id WHERE u.code=? ORDER BY v.step_seq", (code,))

    def batch(self, product="GEAR", qty=None, code=None, at_minute=0):
        return self.store.add_batch(self.ids["products"][product], self.line, qty, code, created_at=self.t0 + M(at_minute))

    def run_gear(self, code, times: dict[str, tuple[float, float]], issue_at: float = 0.0, reject=None):
        """Drive one Gear through the line. times = {station: (enter_min, exit_min)}."""
        self.ev("issue", RAW, issue_at, code, product="GEAR")
        for name, (a, b) in times.items():
            self.ev("enter", name, a, code)
            if reject and reject[0] == name:
                self.ev("reject", name, b, code, reason=reject[1])
                return
            self.ev("exit", name, b, code)
