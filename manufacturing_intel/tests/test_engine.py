import random
import unittest

from manufacturing_intel.blueprint import FINISHED, RAW
from manufacturing_intel.store import StoreError

from .common import Fx, M, T0

GEAR_TIMES = {  # the spec's GEAR-000125 journey (§6), minutes after 09:00
    "Cutting": (10, 24), "CNC Machining": (24, 52), "Drilling": (52, 70), "Grinding": (70, 92),
    "Quality Inspection": (92, 101), "Painting": (101, 125),
}


class Journey(unittest.TestCase):
    def test_full_journey_matches_the_spec_example(self):
        fx = Fx()
        fx.batch()
        fx.run_gear("GEAR-000125", GEAR_TIMES, issue_at=2)
        u = fx.unit("GEAR-000125")
        self.assertEqual(u["status"], "finished")
        self.assertEqual(u["cycle_s"], M(125 - 2))            # 2h03m: issue 09:02 -> finished 11:05
        v = {x["station"]: x for x in fx.visits("GEAR-000125")}
        self.assertEqual(v["Cutting"]["processing_s"], M(14))
        self.assertEqual(v["Cutting"]["waiting_s"], M(8))      # issued 09:02, cutting started 09:10
        self.assertEqual(v["CNC Machining"]["processing_s"], M(28))
        self.assertEqual(v["Painting"]["processing_s"], M(24))
        self.assertEqual(v[FINISHED]["outcome"], "completed")  # no camera on the sink -> automatic
        self.assertEqual(v[RAW]["outcome"], "completed")

    def test_waiting_versus_processing(self):                  # §7: CNC done 10:00, drilling 10:35-10:50
        fx = Fx()
        fx.batch()
        fx.ev("issue", RAW, 0, "G1", product="GEAR")
        fx.ev("enter", "Cutting", 1, "G1"); fx.ev("exit", "Cutting", 20, "G1")
        fx.ev("enter", "CNC Machining", 20, "G1"); fx.ev("exit", "CNC Machining", 60, "G1")
        fx.ev("enter", "Drilling", 95, "G1"); fx.ev("exit", "Drilling", 110, "G1")
        d = {x["station"]: x for x in fx.visits("G1")}["Drilling"]
        self.assertEqual((d["waiting_s"], d["processing_s"]), (M(35), M(15)))

    def test_unit_is_only_in_one_place(self):
        fx = Fx()
        fx.batch()
        fx.ev("issue", RAW, 0, "G1", product="GEAR")
        fx.ev("enter", "Cutting", 1, "G1")
        active = [v for v in fx.visits("G1") if v["outcome"] in ("waiting", "processing")]
        self.assertEqual([(v["station"], v["outcome"]) for v in active], [("Cutting", "processing")])


class Refusals(unittest.TestCase):
    def setUp(self):
        self.fx = Fx()
        self.fx.batch()
        self.fx.ev("issue", RAW, 0, "G1", product="GEAR")

    def counts(self):
        s = self.fx.store
        return tuple(s.scalar(f"SELECT COUNT(*) FROM {t}") for t in ("units", "visits", "batches"))

    def test_duplicate_enter_is_ignored(self):
        self.fx.ev("enter", "Cutting", 1, "G1")
        r = self.fx.ev("enter", "Cutting", 2, "G1")
        self.assertEqual(r["result"], "ignored")
        self.assertEqual(self.fx.visits("G1")[1]["start_ts"], T0 + M(1))   # first enter kept

    def test_exit_without_enter_is_unmatched_and_changes_nothing(self):
        before = self.counts()
        r = self.fx.ev("exit", "Cutting", 5, "G1")
        self.assertEqual(r["result"], "unmatched")
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.fx.visits("G1")[1]["outcome"], "waiting")

    def test_unknown_unit_mid_line_is_unmatched(self):
        self.assertEqual(self.fx.ev("enter", "Drilling", 5, "NOPE-1")["result"], "unmatched")

    def test_out_of_order_is_an_error_and_rolls_back(self):
        self.fx.ev("enter", "Cutting", 10, "G1")
        r = self.fx.ev("exit", "Cutting", 5, "G1")
        self.assertEqual(r["result"], "error")
        self.assertEqual(self.fx.visits("G1")[1]["outcome"], "processing")

    def test_future_timestamp_rejected(self):
        r = self.fx.engine.record("enter", self.fx.st["Cutting"], ts=self.fx.now + 3600, unit_code="G1")
        self.assertEqual(r["result"], "error")
        self.assertEqual(self.fx.store.scalar("SELECT COUNT(*) FROM events WHERE result='error'"), 1)

    def test_bad_inputs(self):
        e = self.fx.engine
        self.assertEqual(e.record("teleport", self.fx.st["Cutting"])["result"], "error")
        self.assertEqual(e.record("enter", 99999)["result"], "error")
        self.assertEqual(e.record("enter", self.fx.st["Cutting"], ts="soon")["result"], "error")
        self.assertEqual(e.record("enter", self.fx.st["Cutting"], ts=5)["result"], "error")

    def test_going_backwards_is_out_of_route_not_rework(self):
        for n, (a, b) in list(GEAR_TIMES.items())[:3]:
            self.fx.ev("enter", n, a, "G1"); self.fx.ev("exit", n, b, "G1")
        r = self.fx.ev("enter", "Cutting", 200, "G1")
        self.assertEqual(r["result"], "ignored")
        self.assertIn("past", r["detail"])

    def test_station_not_on_route(self):
        # a Gear never visits Bending
        self.assertEqual(self.fx.ev("enter", "Bending", 3, "G1")["result"], "ignored")

    def test_finished_unit_ignores_further_events(self):
        for n, (a, b) in GEAR_TIMES.items():
            self.fx.ev("enter", n, a, "G1"); self.fx.ev("exit", n, b, "G1")
        self.assertEqual(self.fx.unit("G1")["status"], "finished")
        self.assertEqual(self.fx.ev("enter", "Painting", 500, "G1")["result"], "ignored")


class FifoMatching(unittest.TestCase):
    def test_camera_style_events_without_codes(self):
        fx = Fx()
        fx.batch()
        for i in range(3):
            fx.ev("issue", RAW, i, product="GEAR")            # three anonymous units
        codes = [u["code"] for u in fx.store.q("SELECT code FROM units ORDER BY id")]
        self.assertEqual(codes, ["GEAR-000001", "GEAR-000002", "GEAR-000003"])
        for i in range(3):
            self.assertEqual(fx.ev("enter", "Cutting", 10 + i)["unit_code"], codes[i])   # oldest first
        self.assertEqual(fx.ev("exit", "Cutting", 30)["unit_code"], codes[0])            # longest inside leaves first
        self.assertEqual(fx.ev("exit", "Cutting", 31)["unit_code"], codes[1])
        self.assertEqual(fx.ev("enter", "CNC Machining", 32)["unit_code"], codes[0])      # first to arrive there

    def test_first_station_auto_issues_a_unit_and_a_batch(self):
        fx = Fx(products=("GEAR",))                            # one route -> the product is unambiguous
        r = fx.ev("enter", "Cutting", 5)                      # camera sees a part, nobody issued it
        self.assertEqual(r["result"], "applied")
        self.assertEqual(r["unit_code"], "GEAR-000001")
        self.assertTrue(fx.store.q1("SELECT code FROM batches")["code"].startswith("AUTO-GEAR-"))
        v = {x["station"]: x for x in fx.visits("GEAR-000001")}
        self.assertEqual(v["Cutting"]["waiting_s"], 0)
        # a second sighting later joins the same auto batch
        fx.ev("enter", "Cutting", 6)
        self.assertEqual(fx.store.scalar("SELECT COUNT(*) FROM batches"), 1)

    def test_ambiguous_product_without_batch_is_unmatched(self):
        fx = Fx()                                              # 3 routes, no active batch, no hint
        self.assertEqual(fx.ev("enter", "Cutting", 5)["result"], "unmatched")
        r = fx.ev("enter", "Cutting", 5, product="SHAFT")
        self.assertEqual(r["result"], "applied")
        self.assertTrue(r["unit_code"].startswith("SHAFT-"))
        b = fx.store.q1("SELECT code FROM batches")
        self.assertTrue(b["code"].startswith("AUTO-SHAFT-"))

    def test_nothing_to_match_is_reported_not_invented(self):
        fx = Fx()
        fx.batch()
        self.assertEqual(fx.ev("exit", "Drilling", 5)["result"], "unmatched")
        self.assertEqual(fx.ev("enter", "Drilling", 5)["result"], "unmatched")   # not the first station


class SkipAhead(unittest.TestCase):
    def test_missed_scans_are_flagged_and_no_times_are_invented(self):
        fx = Fx()
        fx.batch()
        fx.ev("issue", RAW, 0, "G1", product="GEAR")
        r = fx.ev("enter", "Drilling", 50, "G1")               # Cutting and CNC never reported
        self.assertEqual(r["result"], "applied")
        self.assertIn("skipped steps 1,2", r["detail"])
        v = {x["station"]: x for x in fx.visits("G1")}
        self.assertEqual(v["Cutting"]["outcome"], "skipped")
        self.assertIsNone(v["Cutting"]["processing_s"])
        self.assertIsNone(v["Drilling"]["waiting_s"])           # unknown, NOT zero
        self.assertEqual(v["Drilling"]["outcome"], "processing")


class Rejects(unittest.TestCase):
    def test_reject_records_reason_and_closes_unit(self):
        fx = Fx()
        fx.batch()
        fx.run_gear("G1", {"Cutting": (1, 10), "CNC Machining": (11, 30), "Drilling": (31, 40)},
                    reject=("Drilling", "surface_defect"))
        u = fx.unit("G1")
        self.assertEqual(u["status"], "rejected")
        d = {x["station"]: x for x in fx.visits("G1")}["Drilling"]
        self.assertEqual((d["outcome"], d["reject_reason"], d["processing_s"]), ("rejected", "surface_defect", M(9)))

    def test_unknown_reason_becomes_other_and_keeps_the_text(self):
        fx = Fx()
        fx.batch()
        fx.ev("issue", RAW, 0, "G1", product="GEAR"); fx.ev("enter", "Cutting", 1, "G1")
        r = fx.ev("reject", "Cutting", 2, "G1", reason="Tool broke")
        self.assertEqual(r["result"], "applied")
        self.assertIn("Tool broke", r["detail"])
        self.assertEqual({x["station"]: x for x in fx.visits("G1")}["Cutting"]["reject_reason"], "other")

    def test_reject_while_waiting_has_no_processing_time(self):
        fx = Fx()
        fx.batch()
        fx.ev("issue", RAW, 0, "G1", product="GEAR")
        fx.ev("reject", "Cutting", 4, "G1", reason="material_defect")
        v = {x["station"]: x for x in fx.visits("G1")}["Cutting"]
        self.assertIsNone(v["processing_s"])
        self.assertEqual(v["outcome"], "rejected")

    def test_anonymous_reject_prefers_the_unit_being_processed(self):
        fx = Fx()
        fx.batch()
        fx.ev("issue", RAW, 0, product="GEAR"); fx.ev("issue", RAW, 1, product="GEAR")
        fx.ev("enter", "Cutting", 2)                            # unit 1 processing, unit 2 waiting
        self.assertEqual(fx.ev("reject", "Cutting", 3, reason="dimension_issue")["unit_code"], "GEAR-000001")

    def test_no_rejects_at_source_or_sink(self):
        fx = Fx()
        fx.batch()
        self.assertEqual(fx.ev("reject", RAW, 1, "G1")["result"], "ignored")
        self.assertEqual(fx.ev("reject", FINISHED, 1, "G1")["result"], "ignored")


class SinkWithCamera(unittest.TestCase):
    def test_unit_waits_at_a_monitored_sink_until_it_is_seen_there(self):
        fx = Fx()
        cam = fx.store.add_camera("Dock", "video.mp4", fx.line)
        fx.store.update_station(fx.st[FINISHED], camera_id=cam["id"])
        fx.batch()
        fx.run_gear("G1", GEAR_TIMES)
        self.assertEqual(fx.unit("G1")["status"], "in_progress")
        self.assertEqual(fx.ev("enter", FINISHED, 130, "G1")["detail"], "finished")
        u = fx.unit("G1")
        self.assertEqual((u["status"], u["cycle_s"]), ("finished", M(130)))
        self.assertEqual({x["station"]: x for x in fx.visits("G1")}[FINISHED]["waiting_s"], M(5))


class Invariants(unittest.TestCase):
    """Fuzz: a stream of random (often invalid) events must never leave the data inconsistent."""

    def test_random_event_stream(self):
        rnd = random.Random(7)
        fx = Fx()
        fx.batch("GEAR")
        names = ["Cutting", "CNC Machining", "Drilling", "Grinding", "Quality Inspection", "Painting", FINISHED, RAW, "Bending"]
        codes = [f"G{i}" for i in range(12)] + [None]
        minute = 0.0
        for _ in range(1500):
            minute += rnd.random() * 2
            t = rnd.choice(["issue", "enter", "exit", "reject", "enter", "exit"])
            fx.ev(t, rnd.choice(names), minute - (rnd.random() * 30 if rnd.random() < 0.1 else 0),
                  rnd.choice(codes), product="GEAR", reason=rnd.choice(["surface_defect", "x", None]))
        s = fx.store
        for u in s.q("SELECT * FROM units"):
            vs = s.q("SELECT * FROM visits WHERE unit_id=?", (u["id"],))
            active = [v for v in vs if v["outcome"] in ("waiting", "processing")]
            if u["status"] == "in_progress":
                self.assertEqual(len(active), 1, u)
                self.assertEqual(active[0]["step_seq"], u["step_seq"])
            else:
                self.assertEqual(active, [], u)
            for v in vs:
                for f in ("processing_s", "waiting_s"):
                    if v[f] is not None:
                        self.assertGreaterEqual(v[f], 0, (u["code"], v))
                if v["outcome"] == "completed":
                    self.assertIsNotNone(v["end_ts"])
            seqs = sorted(v["step_seq"] for v in vs)
            self.assertEqual(seqs, list(range(len(seqs))), u)          # visits are contiguous from the source
            if u["status"] == "finished":
                self.assertAlmostEqual(u["cycle_s"], u["finished_at"] - u["created_at"])
        self.assertGreater(s.scalar("SELECT COUNT(*) FROM events WHERE result='applied'"), 50)
        self.assertGreater(s.scalar("SELECT COUNT(*) FROM events WHERE result!='applied'"), 50)


class RouteValidation(unittest.TestCase):
    def test_route_rules(self):
        fx = Fx()
        st, p = fx.st, fx.ids["products"]["GEAR"]
        for bad in ([st["Cutting"], st["Drilling"], st[FINISHED]],                       # no source
                    [st[RAW], st["Cutting"], st["Cutting"], st[FINISHED]],               # repeats
                    [st[RAW], st[FINISHED]],                                             # too short
                    [st[RAW], st[FINISHED], st["Cutting"]]):                             # sink not last
            with self.assertRaises(StoreError):
                fx.store.save_route(fx.line, p, bad)
        with self.assertRaises(StoreError):   # can't rewrite a route that already has production
            fx.batch(); fx.ev("issue", RAW, 0, "G1", product="GEAR")
            fx.store.save_route(fx.line, p, [st[RAW], st["Cutting"], st[FINISHED]])

    def test_station_and_product_validation(self):
        fx = Fx()
        with self.assertRaises(StoreError):
            fx.store.add_station(fx.line, "Cutting", "process")           # duplicate name
        with self.assertRaises(StoreError):
            fx.store.add_station(fx.line, "X", "process", expected_process_min=-3)
        with self.assertRaises(StoreError):
            fx.store.add_station(fx.line, "Y", "process", zone=[[0, 0], [2, 0], [1, 1]])
        with self.assertRaises(StoreError):
            fx.store.add_product("bad code!", "x")
        with self.assertRaises(StoreError):
            fx.store.add_batch(fx.ids["products"]["GEAR"], 999)


if __name__ == "__main__":
    unittest.main()
