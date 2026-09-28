import unittest
from datetime import datetime

from manufacturing_intel import periods
from manufacturing_intel.analytics import Analytics
from manufacturing_intel.blueprint import FINISHED, RAW
from manufacturing_intel.periods import Window

from .common import Fx, M, T0

FLOW = ["Cutting", "CNC Machining", "Drilling", "Grinding", "Quality Inspection", "Painting"]
DUR = dict(zip(FLOW, (12, 25, 15, 18, 8, 20)))     # spec §5 average processing minutes
WAIT_MIN = 2


def run_unit(fx, code, base, fate="finished", dur=None, wait=WAIT_MIN, batch=None):
    """One Gear through the line starting at minute `base`. fate is 'finished', ('stuck', station) =
    entered and never left, ('reject', station, reason), or ('queue', station) = arrived, never started."""
    dur = dur or DUR
    t = base
    fx.ev("issue", RAW, t, code, product="GEAR", batch=batch)
    for name in FLOW:
        t += wait
        if isinstance(fate, tuple) and fate[1] == name and fate[0] == "queue":
            return
        fx.ev("enter", name, t, code)
        if isinstance(fate, tuple) and fate[1] == name and fate[0] == "stuck":
            return
        t += dur[name]
        if isinstance(fate, tuple) and fate[1] == name and fate[0] == "reject":
            fx.ev("reject", name, t, code, reason=fate[2])
            return
        fx.ev("exit", name, t, code)


def spec_day(fx):
    """Exactly the spec's §5 table: 100 in; 5 still at Cutting, 5 at CNC; rejects 2/3/3/2; 80 finished."""
    fates = ([("stuck", "Cutting")] * 5 + [("stuck", "CNC Machining")] * 5 + [("reject", "Drilling", "dimension_issue")] * 2
             + [("reject", "Grinding", "surface_defect")] * 3 + [("reject", "Quality Inspection", "quality_failure")] * 3
             + [("reject", "Painting", "surface_defect")] * 2 + ["finished"] * 80)
    for i, f in enumerate(fates):
        run_unit(fx, f"GEAR-{i + 1:06d}", base=i * 1.0, fate=f)


def big_window():
    return Window(T0 - 60, T0 + 20 * 3600, "test", "custom")


class SpecTable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = Fx(now=T0 + 24 * 3600)
        cls.fx.batch()
        spec_day(cls.fx)
        cls.a = Analytics(cls.fx.store, clock=lambda: cls.fx.now)
        cls.stations = {s["name"]: s for s in cls.a.stations(big_window(), cls.fx.line, cls.fx.ids["products"]["GEAR"])}

    def test_stage_table_matches_spec_section_5(self):
        s = self.stations
        expect = {  # entered, completed, rejected (spec table)
            RAW: (100, 100, 0), "Cutting": (100, 95, 0), "CNC Machining": (95, 90, 0), "Drilling": (90, 88, 2),
            "Grinding": (88, 85, 3), "Quality Inspection": (85, 82, 3), "Painting": (82, 80, 2), FINISHED: (80, 80, 0)}
        for name, (e, c, r) in expect.items():
            self.assertEqual((s[name]["entered"], s[name]["completed"], s[name]["rejected"]), (e, c, r), name)

    def test_average_processing_times(self):
        for name, mins in DUR.items():
            self.assertAlmostEqual(self.stations[name]["avg_processing_s"], mins * 60, places=6, msg=name)

    def test_remaining_products(self):
        self.assertEqual(self.stations["Cutting"]["processing"], 5)         # "5 remaining"
        self.assertEqual(self.stations["CNC Machining"]["processing"], 5)   # "5 in process"
        self.assertEqual(self.stations["Drilling"]["waiting"], 0)

    def test_line_kpis(self):
        k = self.a.kpis(big_window(), self.fx.line)
        self.assertEqual((k["issued"], k["finished"], k["rejected"], k["in_production"]), (100, 80, 10, 10))
        self.assertEqual(k["started_to_finished_pct"], 80.0)                # the spec's "yield 80%"
        self.assertEqual(k["yield_pct"], 88.9)                              # finished / (finished + rejected)
        self.assertAlmostEqual(k["avg_cycle_s"], M(6 * WAIT_MIN + sum(DUR.values())))
        self.assertEqual(k["cohort"], {"started": 100, "finished": 80, "rejected": 10, "in_progress": 10})

    def test_conservation(self):
        """entered = completed + rejected + waiting + processing at every process stage (nothing lost)."""
        for name, s in self.stations.items():
            if s["kind"] == "process":
                self.assertEqual(s["entered"], s["completed"] + s["rejected"] + s["waiting"] + s["processing"], name)

    def test_rejection_rates_and_reasons(self):
        self.assertEqual(self.stations["Drilling"]["rejection_rate_pct"], 2.2)
        reasons = {(r["station"], r["reason"]): r["n"] for r in self.a.rejection_reasons(big_window(), self.fx.line)}
        self.assertEqual(reasons[("Grinding", "surface_defect")], 3)
        self.assertEqual(sum(reasons.values()), 10)

    def test_breakdowns_add_up(self):
        for by in ("day", "shift", "product", "line", "batch"):
            rows = self.a.breakdown(by, big_window(), self.fx.line)
            self.assertEqual(sum(r["started"] for r in rows), 100, by)
            self.assertEqual(sum(r["finished"] for r in rows), 80, by)

    def test_efficiency_and_utilisation_are_sane(self):
        s = self.stations["CNC Machining"]
        self.assertAlmostEqual(s["efficiency_pct"], 100.0)                  # ran exactly at the expected 25 min
        self.assertTrue(0 < s["utilization_pct"] <= 100)


class Bottleneck(unittest.TestCase):
    def setUp(self):
        self.fx = Fx(now=T0 + M(300))                   # "now" = minute 300, so "last hour" = minutes 240-300
        self.fx.batch(at_minute=0)
        self.a = Analytics(self.fx.store, clock=lambda: self.fx.now)

    def flow(self, n, base, dur, wait, fate="finished", first=0):
        for i in range(n):
            run_unit(self.fx, f"GEAR-{first + i + 1:06d}", base + i * 0.5, fate, dur, wait)

    def test_queue_in_front_of_drilling_is_the_bottleneck(self):
        # 20 units left CNC in the last hour and are waiting at Drilling (expected: up to 15)
        self.flow(20, 250, DUR, 1, fate=("queue", "Drilling"))
        b = self.a.bottleneck(self.fx.line)
        self.assertEqual((b["station"], b["level"], b["waiting"], b["expected_waiting"]), ("Drilling", "bottleneck", 20, 15))
        self.assertIn("20 units waiting", b["evidence"])
        self.assertNotIn("because", b["evidence"])                          # observations only, no invented cause

    def test_small_queue_is_not_a_bottleneck(self):
        self.flow(8, 250, DUR, 1, fate=("queue", "Drilling"))
        self.assertIsNone(self.a.bottleneck(self.fx.line))

    def test_potential_bottleneck_from_rates(self):
        # 20 units reach Grinding in the last hour, 12 come out, 8 wait there (limit 10, so not over it)
        fast = {**DUR, "Cutting": 1, "CNC Machining": 1, "Drilling": 1}
        self.flow(12, 236, fast, 0.5)
        self.flow(8, 242, fast, 0.5, fate=("queue", "Grinding"), first=12)
        b = self.a.bottleneck(self.fx.line)
        self.assertEqual((b["station"], b["level"], b["waiting"]), ("Grinding", "potential", 8))

    def test_worst_queue_wins(self):
        self.flow(20, 250, DUR, 1, fate=("queue", "Drilling"))
        self.flow(40, 200, DUR, 1, fate=("queue", "Grinding"), first=20)
        self.assertEqual(self.a.bottleneck(self.fx.line)["station"], "Grinding")   # 40/10 > 20/15

    def test_peak_waiting_is_recovered_from_history(self):
        self.flow(20, 60, DUR, 1, fate="finished")
        w = Window(T0, T0 + M(300), "t", "custom")
        peak = {s["name"]: s["peak_waiting"] for s in self.a.stations(w, self.fx.line)}
        self.assertGreaterEqual(peak["Cutting"], 2)


class Comparison(unittest.TestCase):
    def test_drilling_slowdown_is_ranked_first(self):
        fx = Fx(now=T0 + 3 * 86400)
        fx.batch(at_minute=0)
        slow = {**DUR, "Drilling": 23}
        for i in range(30):                       # day 1: normal
            run_unit(fx, f"GEAR-{i + 1:06d}", i * 2.0, dur=DUR, wait=2)
        for i in range(30):                       # day 2: Drilling takes 23 min instead of 15 and everything waits longer
            run_unit(fx, f"GEAR-{i + 101:06d}", 1440 + i * 2.0, dur=slow, wait=6)
        a = Analytics(fx.store, clock=lambda: fx.now)
        d1, d2 = Window(T0, T0 + 86400, "day1", "custom"), Window(T0 + 86400, T0 + 2 * 86400, "day2", "custom")
        c = a.compare(d2, d1, fx.line)
        top = c["stages"][0]
        self.assertEqual(top["station"], "Drilling")                       # +8 min processing on top of the wait
        self.assertAlmostEqual(top["d_avg_processing_s"], M(8))
        self.assertAlmostEqual(top["added_time_per_unit_s"], M(8 + 4))
        cyc = next(r for r in c["kpis"] if r["metric"] == "avg_cycle_s")
        self.assertGreater(cyc["delta"], 0)


class Periods(unittest.TestCase):
    SHIFTS = [{"name": "Morning", "start": "06:00", "end": "14:00"}, {"name": "Afternoon", "start": "14:00", "end": "22:00"},
              {"name": "Night", "start": "22:00", "end": "06:00"}]

    def at(self, y, m, d, hh, mm=0):
        return datetime(y, m, d, hh, mm).timestamp()

    def test_today_yesterday(self):
        now = self.at(2026, 9, 28, 14, 30)
        t, y = periods.resolve("today", now, self.SHIFTS), periods.resolve("yesterday", now, self.SHIFTS)
        self.assertEqual(t.start, self.at(2026, 9, 28, 0))
        self.assertEqual(y.start, self.at(2026, 9, 27, 0))
        self.assertEqual(y.end, t.start)

    def test_like_for_like_comparison_cuts_yesterday_at_the_same_time(self):
        now = self.at(2026, 9, 28, 14, 30)
        t = periods.resolve("today", now, self.SHIFTS)
        p = periods.previous(t, now, self.SHIFTS)
        self.assertEqual(p.end, self.at(2026, 9, 27, 14, 30))
        self.assertIn("14:30", p.label)
        self.assertEqual(periods.previous(t, now, self.SHIFTS, like_for_like=False).end, t.start)

    def test_finished_period_compares_with_full_previous(self):
        now = self.at(2026, 9, 28, 14, 30)
        y = periods.resolve("yesterday", now, self.SHIFTS)
        p = periods.previous(y, now, self.SHIFTS)
        self.assertEqual((p.start, p.end), (self.at(2026, 9, 26, 0), self.at(2026, 9, 27, 0)))

    def test_week_starts_monday(self):
        now = self.at(2026, 9, 30, 10)                      # a Wednesday
        w = periods.resolve("this_week", now, self.SHIFTS)
        self.assertEqual(datetime.fromtimestamp(w.start).weekday(), 0)
        self.assertEqual(datetime.fromtimestamp(w.start).day, 28)

    def test_months(self):
        now = self.at(2026, 3, 15, 10)
        lm = periods.resolve("last_month", now, self.SHIFTS)
        self.assertEqual((datetime.fromtimestamp(lm.start).month, datetime.fromtimestamp(lm.end).month), (2, 3))

    def test_shifts_including_the_overnight_one(self):
        now = self.at(2026, 9, 28, 3, 0)                    # 03:00 -> Night shift that started yesterday 22:00
        cur = periods.resolve("current_shift", now, self.SHIFTS)
        self.assertEqual((cur.start, cur.end), (self.at(2026, 9, 27, 22), self.at(2026, 9, 28, 6)))
        prev = periods.resolve("previous_shift", now, self.SHIFTS)
        self.assertEqual((prev.start, prev.end), (self.at(2026, 9, 27, 14), self.at(2026, 9, 27, 22)))
        self.assertEqual(periods.shift_of(self.at(2026, 9, 28, 23), self.SHIFTS), "Night")
        self.assertEqual(periods.shift_of(self.at(2026, 9, 28, 9), self.SHIFTS), "Morning")

    def test_custom_validation(self):
        now = self.at(2026, 9, 28, 12)
        w = periods.resolve("custom", now, self.SHIFTS, "2026-09-01", "2026-09-03")
        self.assertEqual(w.end - w.start, 3 * 86400)               # end date inclusive
        for bad in (("2026-09-05", "2026-09-01"), ("x", "y"), (None, None), ("2020-01-01", "2026-01-01")):
            with self.assertRaises(periods.PeriodError):
                periods.resolve("custom", now, self.SHIFTS, *bad)
        with self.assertRaises(periods.PeriodError):
            periods.resolve("fortnight", now, self.SHIFTS)


class Journey(unittest.TestCase):
    def test_journey_and_batch_flow(self):
        fx = Fx()
        b = fx.batch(qty=5)
        for i in range(5):
            run_unit(fx, f"GEAR-{i + 1:06d}", i, fate="finished" if i < 3 else ("reject", "Grinding", "surface_defect"))
        a = Analytics(fx.store, clock=lambda: fx.now)
        j = a.journey("gear-000001")
        self.assertEqual(j["unit"]["status"], "finished")
        self.assertEqual([v["station"] for v in j["visits"]][0], RAW)
        self.assertEqual(len(j["visits"]), 8)
        self.assertAlmostEqual(j["totals"]["processing_s"], M(sum(DUR.values())))
        self.assertIsNone(a.journey("nope"))
        flow = a.batch_flow(b["id"])
        by = {s["station"]: s for s in flow["stages"]}
        self.assertEqual((by["Grinding"]["reached"], by["Grinding"]["rejected"], by["Painting"]["reached"]), (5, 2, 3))
        self.assertEqual(by[FINISHED]["reached"], 3)
        self.assertEqual(a.units(fx.line, status="rejected")["total"], 2)
        self.assertEqual(a.units(fx.line, q="GEAR-00000")["total"], 5)


class DataQuality(unittest.TestCase):
    def test_refusals_are_counted(self):
        fx = Fx()
        fx.batch()
        fx.ev("issue", RAW, 0, "G1", product="GEAR")
        fx.ev("exit", "Cutting", 1, "G1")            # unmatched
        fx.ev("enter", "Cutting", 2, "G1")
        fx.ev("enter", "Cutting", 3, "G1")           # ignored duplicate
        a = Analytics(fx.store, clock=lambda: fx.now)
        q = a.data_quality(big_window(), fx.line)
        self.assertEqual((q["applied"], q["ignored"], q["unmatched"]), (2, 1, 1))
        self.assertEqual(q["problem_pct"], 25.0)


if __name__ == "__main__":
    unittest.main()
