import unittest
from datetime import datetime

from manufacturing_intel.analytics import Analytics
from manufacturing_intel.assistant import Assistant

from .common import Fx, M
from .test_analytics import DUR, run_unit

TODAY9 = datetime(2026, 9, 28, 9, 0).timestamp()          # 09:00 local, a Monday
NOW = datetime(2026, 9, 28, 20, 0).timestamp()


def build(second_line=False, drilling_today=15, today_units=100, yesterday_units=100, rejects=True):
    fx = Fx(lines=("Line A", "Line B") if second_line else ("Line A",), now=NOW, t0=TODAY9)
    fx.batch(at_minute=-1440)
    fx.batch(at_minute=0)
    # yesterday: everything finishes
    for i in range(yesterday_units):
        run_unit(fx, f"GEAR-{i + 1:06d}", -1440 + i * 1.0, batch=None)
    fates = ([("reject", "Grinding", "surface_defect")] * 3 + [("reject", "Quality Inspection", "quality_failure")] * 4
             + [("reject", "Drilling", "dimension_issue")] * 2) if rejects else []
    for i in range(today_units):
        fate = fates[i] if i < len(fates) else "finished"
        run_unit(fx, f"GEAR-{i + 1001:06d}", i * 1.0, fate=fate, dur={**DUR, "Drilling": drilling_today})
    return fx, Assistant(Analytics(fx.store, clock=lambda: fx.now))


class Answers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx, cls.bot = build()

    def ask(self, q):
        return self.bot.ask(q)

    def test_finished_count_states_its_scope(self):
        r = self.ask("How many products were completed today?")
        self.assertEqual(r["intent"], "finished")
        self.assertIn("91 products were completed in Today", r["answer"])
        self.assertIn("9 rejected", r["answer"])
        self.assertIn("Line A", r["scope"])

    def test_yesterday_is_understood(self):
        r = self.ask("how many products were completed yesterday")
        self.assertIn("100 products were completed in Yesterday", r["answer"])

    def test_moved_between_stations(self):
        r = self.ask("How many products moved from CNC to Drilling today?")
        self.assertEqual(r["intent"], "moved")
        self.assertIn("100 products moved from CNC Machining to Drilling", r["answer"])

    def test_average_production_time_vs_target(self):
        r = self.ask("What is the average production time today?")
        self.assertEqual(r["intent"], "cycle")
        self.assertIn("target of 2h 00m", r["answer"])

    def test_longest_stage(self):
        r = self.ask("Which stage takes the longest?")
        self.assertIn("CNC Machining takes the longest", r["answer"])

    def test_highest_rejection_rate(self):
        r = self.ask("Which stage has the highest rejection rate?")
        self.assertIn("Quality Inspection has the highest rejection rate", r["answer"])     # 4 of the 95 units that reached it
        self.assertIn("4.2%", r["answer"])

    def test_rejected_count_and_reasons(self):
        r = self.ask("How many products were rejected today?")
        self.assertIn("9 products were rejected in Today", r["answer"])
        self.assertTrue(any("quality failure" in e[0] for e in r["evidence"]))

    def test_yield_explains_its_definition(self):
        r = self.ask("What is the production yield today")
        self.assertIn("91 finished, 9 rejected", r["answer"])
        self.assertIn("yield for Today: 91%", r["answer"])

    def test_unit_lookup(self):
        r = self.ask("Where is GEAR-001050?")
        self.assertEqual(r["intent"], "unit")
        self.assertIn("finished after", r["answer"])
        self.assertEqual(self.ask("where is gear-999999")["intent"], "unit")
        self.assertIn("don't have", self.ask("where is gear-999999")["answer"])

    def test_no_bottleneck_when_calm(self):
        r = self.ask("Where is the current bottleneck?")
        self.assertEqual(r["intent"], "bottleneck")
        self.assertIn("No bottleneck right now", r["answer"])

    def test_wip_and_throughput(self):
        self.assertEqual(self.ask("What is the current production quantity?")["intent"], "wip")
        self.assertIn("0 products are in production", self.ask("what is currently in production")["answer"])
        self.assertEqual(self.ask("what is the throughput today")["intent"], "throughput")

    def test_waiting_time_between(self):
        r = self.ask("What is the average waiting time between CNC and Drilling?")
        self.assertEqual(r["intent"], "waiting")
        self.assertIn("2.0 min", r["answer"])

    def test_unknown_questions_get_help_not_a_guess(self):
        for q in ("what's the weather like", "tell me a joke", "", "x" * 600):
            r = self.ask(q)
            self.assertEqual(r["intent"], "help", q[:20])
            self.assertTrue(r["suggestions"])


class Why(unittest.TestCase):
    def test_reports_the_stage_that_changed_without_inventing_a_cause(self):
        fx, bot = build(drilling_today=23, rejects=False, today_units=90, yesterday_units=100)
        r = bot.ask("Why is today's production lower than yesterday?")
        self.assertEqual(r["intent"], "why")
        a = r["answer"]
        self.assertIn("Difference: -10 units", a)
        self.assertIn("largest observed change was at Drilling", a)
        self.assertIn("15.0 min -> 23.0 min", a)
        self.assertNotIn("because", a.lower())
        self.assertIn("operational cause", a)
        self.assertIn("Drilling", [row[0] for row in r["table"]["rows"]])

    def test_says_when_nothing_changed(self):
        fx, bot = build(rejects=False, today_units=100, yesterday_units=100)
        r = bot.ask("why is production slower today than yesterday")
        self.assertIn("No stage shows", r["answer"])


class LikeForLike(unittest.TestCase):
    def test_a_named_yesterday_is_cut_at_the_same_time_of_day(self):
        now = datetime(2026, 9, 28, 12, 0).timestamp()
        fx = Fx(now=now, t0=TODAY9)
        fx.batch(at_minute=-1440); fx.batch(at_minute=0)
        for i in range(100):                                   # yesterday: a unit a minute from 09:00, 110 min each
            run_unit(fx, f"GEAR-{i + 1:06d}", -1440 + i * 1.0)
        for i in range(60):                                    # today so far: the same pace
            run_unit(fx, f"GEAR-{i + 1001:06d}", i * 1.0)
        bot = Assistant(Analytics(fx.store, clock=lambda: fx.now))
        for q in ("Compare today's production with yesterday", "Why is today's production lower than yesterday?"):
            a = bot.ask(q)["answer"]
            self.assertIn("60", a, q)
            self.assertIn("70", a, q)                          # yesterday's *finished* count up to 12:00
            self.assertNotIn("100 finished", a, q)             # not the 100 finished over the whole day
            self.assertNotIn("Yesterday: 100 units", a, q)


class Comparisons(unittest.TestCase):
    def test_compare_two_lines(self):
        fx, bot = build(second_line=True, rejects=False)
        r = bot.ask("Compare Line A and Line B")
        self.assertEqual(r["intent"], "compare_lines")
        self.assertIn("Line A finished 100 vs Line B 0", r["answer"])

    def test_compare_today_with_yesterday(self):
        fx, bot = build(rejects=False, today_units=80)
        r = bot.ask("Compare today's production with yesterday")
        self.assertEqual(r["intent"], "compare_periods")
        self.assertIn("Today: 80 finished vs 100", r["answer"])
        self.assertIn("(-20)", r["answer"])

    def test_bottleneck_is_reported_with_the_numbers(self):
        fx = Fx(now=NOW, t0=TODAY9)
        fx.batch(at_minute=0)
        base = (NOW - TODAY9) / 60.0 - 50                     # units that left CNC within the last hour
        for i in range(25):
            run_unit(fx, f"GEAR-{i + 1:06d}", base - 30 + i * 0.4, fate=("queue", "Drilling"), dur={**DUR, "Cutting": 2, "CNC Machining": 2}, wait=1)
        bot = Assistant(Analytics(fx.store, clock=lambda: fx.now))
        r = bot.ask("Where is the current bottleneck?")
        self.assertIn("Bottleneck: Drilling", r["answer"])
        self.assertIn("25 units waiting", r["answer"])
        self.assertIn("not why", r["answer"])


if __name__ == "__main__":
    unittest.main()
