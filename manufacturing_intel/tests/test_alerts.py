import unittest

from manufacturing_intel.alerts import AlertEngine
from manufacturing_intel.analytics import Analytics
from manufacturing_intel.blueprint import RAW

from .common import Fx, M, T0
from .test_analytics import DUR, run_unit

NOW_MIN = 600


class AlertBase(unittest.TestCase):
    def setUp(self):
        self.fx = Fx(now=T0 + M(NOW_MIN))
        self.fx.batch(at_minute=0)
        self.a = Analytics(self.fx.store, clock=lambda: self.fx.now)
        self.al = AlertEngine(self.fx.store, self.a, clock=lambda: self.fx.now)
        self.n = 0

    def units(self, count, base, step=0.5, **kw):
        for i in range(count):
            self.n += 1
            run_unit(self.fx, f"GEAR-{self.n:06d}", base + i * step, **kw)

    def kinds(self):
        return sorted((a["kind"], a["station_name"]) for a in self.fx.store.list_alerts() if a["status"] != "resolved")


class Bottleneck(AlertBase):
    def test_bottleneck_alert_opens_carries_evidence_and_resolves(self):
        self.units(20, 550, fate=("queue", "Drilling"), dur=DUR, wait=1)      # arrivals 590-599? -> see below
        r = self.al.evaluate()
        self.assertIn(("bottleneck", "Drilling"), self.kinds())
        a = next(a for a in self.fx.store.list_alerts() if a["kind"] == "bottleneck")
        self.assertEqual(a["evidence"]["waiting"], 20)
        self.assertEqual(a["evidence"]["expected_waiting"], 15)
        self.assertEqual(a["severity"], "medium")                             # 20 <= 2 x 15
        self.assertIn("20 products are waiting", a["message"])

    def test_is_deduplicated_and_ack_is_kept(self):
        self.units(20, 500, fate=("queue", "Drilling"), dur=DUR, wait=1)
        self.al.evaluate()
        r2 = self.al.evaluate()
        self.assertEqual(r2["opened"], 0)
        alerts = [a for a in self.fx.store.list_alerts() if a["kind"] == "bottleneck"]
        self.assertEqual(len(alerts), 1)
        self.fx.store.ack_alert(alerts[0]["id"])
        self.al.evaluate()
        self.assertEqual(self.fx.store.list_alerts()[0]["status"], "ack")     # stays acknowledged while it persists

    def test_clears_when_the_queue_is_worked_off_and_reopens_as_a_new_alert(self):
        self.units(20, 500, fate=("queue", "Drilling"), dur=DUR, wait=1)
        self.al.evaluate()
        # the queue is cleared: every waiting unit starts at Drilling
        self.fx.now = T0 + M(NOW_MIN + 10)
        for i in range(1, 21):
            self.fx.ev("enter", "Drilling", NOW_MIN + 1 + i * 0.1, f"GEAR-{i:06d}")
        r = self.al.evaluate()
        # a queue of 20 left alone for an hour is a bottleneck, idle and below target all at once - all three clear together
        self.assertEqual((r["resolved"], r["active"]), (3, 0))
        rows = self.fx.store.list_alerts()
        self.assertEqual({a["status"] for a in rows}, {"resolved"})
        self.assertTrue(all(a["resolved_at"] for a in rows))
        # ... and when it builds up again it is a new record
        self.n = 100
        self.fx.now = T0 + M(NOW_MIN + 60)
        self.units(20, NOW_MIN + 5, fate=("queue", "Drilling"), dur=DUR, wait=0.2)
        self.al.evaluate()
        self.assertEqual(len([a for a in self.fx.store.list_alerts() if a["kind"] == "bottleneck"]), 2)

    def test_severity_high_when_far_over(self):
        self.units(35, 500, step=0.2, fate=("queue", "Drilling"), dur=DUR, wait=1)
        self.al.evaluate()
        self.assertEqual(next(a for a in self.fx.store.list_alerts() if a["kind"] == "bottleneck")["severity"], "high")


class Delay(AlertBase):
    def slow(self, n):
        self.units(n, 480, step=1, dur={**DUR, "Drilling": 25}, wait=1)

    def test_delay_needs_enough_samples_then_reports_variance(self):
        self.slow(4)
        self.al.evaluate()
        self.assertNotIn(("delay", "Drilling"), self.kinds())
        self.slow(2)
        self.al.evaluate()
        a = next(a for a in self.fx.store.list_alerts() if a["kind"] == "delay")
        self.assertEqual(a["station_name"], "Drilling")
        self.assertAlmostEqual(a["evidence"]["variance_s"], M(10))            # 25 vs expected 15
        self.assertIn("25.0 min vs expected 15 min", a["message"])

    def test_within_tolerance_is_quiet(self):
        self.units(10, 480, step=1, dur={**DUR, "Drilling": 16}, wait=1)      # +6.7% < 15%
        self.al.evaluate()
        self.assertNotIn("delay", [k for k, _ in self.kinds()])


class Quality(AlertBase):
    def test_rejection_rate_over_threshold(self):
        self.units(17, 480, step=1, dur=DUR, wait=1)
        self.units(3, 500, step=1, dur=DUR, wait=1, fate=("reject", "Grinding", "surface_defect"))
        self.al.evaluate()
        a = next(a for a in self.fx.store.list_alerts() if a["kind"] == "quality")
        self.assertEqual(a["station_name"], "Grinding")
        self.assertEqual(a["evidence"]["rejection_rate_pct"], 15.0)           # 3 / 20 vs threshold 5%
        self.assertEqual(a["severity"], "high")                               # > 2 x threshold

    def test_too_few_units_to_judge(self):
        self.units(16, 480, step=1, dur=DUR, wait=1)
        self.units(3, 500, step=1, dur=DUR, wait=1, fate=("reject", "Grinding", "surface_defect"))   # 3/19 = 15.8% but n < 20
        self.al.evaluate()
        self.assertNotIn("quality", [k for k, _ in self.kinds()])


class Idle(AlertBase):
    def test_work_waiting_with_no_activity(self):
        # 3 units reached Grinding 30+ minutes ago and nothing has happened there since
        self.units(3, 380, step=1, fate=("queue", "Grinding"), dur={**DUR, "Cutting": 1, "CNC Machining": 1, "Drilling": 1}, wait=1)
        self.al.evaluate()
        a = next(a for a in self.fx.store.list_alerts() if a["kind"] == "idle")
        self.assertEqual(a["station_name"], "Grinding")
        self.assertGreaterEqual(a["evidence"]["idle_min"], 25)

    def test_a_busy_machine_is_not_idle(self):
        self.units(3, 380, step=1, fate=("queue", "Grinding"), dur={**DUR, "Cutting": 1, "CNC Machining": 1, "Drilling": 1}, wait=1)
        self.n += 1
        # a fourth unit has been *inside* Grinding for a long time (long process, no events)
        run_unit(self.fx, "GEAR-000900", 385, fate=("stuck", "Grinding"), dur={**DUR, "Cutting": 1, "CNC Machining": 1, "Drilling": 1}, wait=1)
        self.al.evaluate()
        self.assertNotIn("idle", [k for k, _ in self.kinds()])

    def test_recent_arrival_is_not_idle_yet(self):
        self.units(2, 585, step=1, fate=("queue", "Grinding"), dur={**DUR, "Cutting": 1, "CNC Machining": 1, "Drilling": 1}, wait=1)
        self.al.evaluate()
        self.assertNotIn("idle", [k for k, _ in self.kinds()])

    def test_nothing_waiting_is_not_reported_as_idle(self):
        self.al.evaluate()
        self.assertEqual(self.kinds(), [])


class LowProduction(AlertBase):
    def test_below_target_with_work_available(self):
        # Cutting target = 24/h; only 8 finished in the last hour while 30 units arrived
        self.units(8, 545, step=0.5, dur={**DUR, "Cutting": 3}, wait=0.5)
        self.units(22, 545, step=0.5, fate=("stuck", "Cutting"), dur=DUR, wait=0.5)
        self.al.evaluate()
        lows = [a for a in self.fx.store.list_alerts() if a["kind"] == "low_production" and a["station_name"] == "Cutting"]
        self.assertEqual(len(lows), 1)
        self.assertEqual(lows[0]["evidence"]["target_per_h"], 24.0)

    def test_a_quiet_factory_or_no_demand_is_not_low_production(self):
        self.al.evaluate()
        self.assertEqual(self.kinds(), [])
        self.units(3, 570, step=1, dur={**DUR, "Cutting": 2}, wait=0.5)     # only 3 arrived: demand-limited, not a failure
        self.al.evaluate()
        self.assertNotIn("low_production", [k for k, _ in self.kinds()])


if __name__ == "__main__":
    unittest.main()
