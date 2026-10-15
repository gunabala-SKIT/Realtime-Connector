"""The activity engine's rule logic, with deterministic synthetic scenarios - appropriate here,
not a shortcut: you need exact control over "movement happened for exactly N seconds" to test
temporal/movement thresholds properly, which real footage can't reliably give on demand. Real
object detection and tracking are already proven against real photos in test_detector.py and
test_tracking.py; what's new and needs proving here is the *rule engine's own logic*.
"""
import unittest
from dataclasses import dataclass

from garment_cv.backend.activities.config import ActivityConfig
from garment_cv.backend.activities.engine import ActivityEngine
from garment_cv.backend.activities.rules import IDLE, RULES, REACHABLE_NOW

FRAME = (1000, 1000)


@dataclass(frozen=True)
class FakeDet:
    cls: str
    box: tuple
    track_id: int | None = None
    zone: str | None = None
    zone_activity: str | None = None
    conf: float = 0.9


def cfg(**kw) -> ActivityConfig:
    base = dict(enabled=True, cutting_min_s=3.0, marking_min_s=3.0, stitching_min_s=5.0,
               piece_handling_min_s=2.0, idle_min_s=10.0, grace_s=1.5, movement_window_s=2.0,
               movement_min_fraction=0.03, attribution_margin=0.3, max_history=200)
    base.update(kw)
    return ActivityConfig(**base)


def person(track_id, box=(100, 100, 200, 400), zone="Cutting Zone", zone_activity="cutting") -> FakeDet:
    return FakeDet("person", box, track_id, zone, zone_activity)


def scissors(box, conf=0.9) -> FakeDet:
    return FakeDet("scissors", box, None, None, None, conf)


def moving_scissors_boxes(base_x=120, step=80, n=20):
    """A scissors box near a person's hand, sweeping sideways each tick - "movement consistent
    with cutting" per spec section 3, computed the simple, rule-based way section 11 asks for.
    `step` must clear the default config's movement threshold (3% of a 1000x1000 frame's
    diagonal ~= 42px) comfortably, with margin for the box's own width."""
    return [(base_x + (i % 2) * step, 200, base_x + 30 + (i % 2) * step, 230) for i in range(n)]


class RulesTable(unittest.TestCase):
    def test_only_cutting_is_reachable_with_the_coco_stand_in(self):
        self.assertEqual(REACHABLE_NOW, frozenset({"CUTTING"}))
        for rule in RULES:
            if rule.name != "CUTTING":
                self.assertNotIn(rule.name, REACHABLE_NOW)


class CuttingRule(unittest.TestCase):
    def test_confirms_after_the_minimum_duration_of_real_movement(self):
        e = ActivityEngine(cfg(cutting_min_s=3.0))
        boxes = moving_scissors_boxes()
        t = 0.0
        result = {}
        for box in boxes:
            result = e.update("cam-1", [person(1), scissors(box)], t, *FRAME)
            t += 0.5
        self.assertEqual(result[1], "CUTTING")

    def test_does_not_confirm_before_the_minimum_duration(self):
        e = ActivityEngine(cfg(cutting_min_s=10.0))
        t = 0.0
        result = {}
        for box in moving_scissors_boxes(n=6):  # 6 * 0.5s = 3s, well under a 10s requirement
            result = e.update("cam-1", [person(1), scissors(box)], t, *FRAME)
            t += 0.5
        self.assertIsNone(result[1])

    def test_scissors_present_but_not_moving_never_confirms(self):
        """Spec section 3, word for word: "Worker holding scissors ≠ Cutting"."""
        e = ActivityEngine(cfg(cutting_min_s=2.0))
        t = 0.0
        result = {}
        for _ in range(20):
            result = e.update("cam-1", [person(1), scissors((150, 200, 180, 230))], t, *FRAME)  # same box every time
            t += 0.5
        self.assertIsNone(result[1])

    def test_scissors_outside_the_cutting_zone_never_confirms(self):
        e = ActivityEngine(cfg(cutting_min_s=1.0))
        t = 0.0
        result = {}
        for box in moving_scissors_boxes(n=10):
            result = e.update("cam-1", [person(1, zone="Break Room", zone_activity=None), scissors(box)], t, *FRAME)
            t += 0.5
        self.assertIsNone(result[1])

    def test_scissors_far_from_the_person_are_not_attributed_to_them(self):
        e = ActivityEngine(cfg(cutting_min_s=1.0))
        far_boxes = [(900 + (i % 2) * 20, 900, 930 + (i % 2) * 20, 930) for i in range(10)]  # opposite corner
        t = 0.0
        result = {}
        for box in far_boxes:
            result = e.update("cam-1", [person(1), scissors(box)], t, *FRAME)
            t += 0.5
        self.assertIsNone(result[1])

    def test_a_short_gap_in_detection_does_not_reset_the_timer(self):
        """A hand briefly blocking the view for one tick must not restart the 3-second clock -
        this is the same lesson as PPE's confirm-then-cooldown and footfall's debounce elsewhere
        in this project: transient misses are normal, not a reason to lose confirmed progress."""
        e = ActivityEngine(cfg(cutting_min_s=3.0, grace_s=1.5))
        t = 0.0
        boxes = moving_scissors_boxes(n=4)  # 2 seconds of real movement
        for box in boxes:
            e.update("cam-1", [person(1), scissors(box)], t, *FRAME)
            t += 0.5
        # one tick with no scissors at all (occlusion) - well within the 1.5s grace period
        e.update("cam-1", [person(1)], t, *FRAME)
        t += 0.5
        result = {}
        for box in moving_scissors_boxes(n=4):
            result = e.update("cam-1", [person(1), scissors(box)], t, *FRAME)
            t += 0.5
        self.assertEqual(result[1], "CUTTING")  # confirmed using the pre-gap progress too

    def test_a_long_gap_does_reset_the_timer(self):
        e = ActivityEngine(cfg(cutting_min_s=3.0, grace_s=0.5))
        t = 0.0
        for box in moving_scissors_boxes(n=4):
            e.update("cam-1", [person(1), scissors(box)], t, *FRAME)
            t += 0.5
        t += 5.0  # far beyond the 0.5s grace period
        result = {}
        for box in moving_scissors_boxes(n=4):  # needs to restart from scratch: not enough by itself
            result = e.update("cam-1", [person(1), scissors(box)], t, *FRAME)
            t += 0.5
        self.assertIsNone(result[1])

    def test_confidence_is_the_average_over_the_activity(self):
        e = ActivityEngine(cfg(cutting_min_s=1.0))
        t = 0.0
        boxes = moving_scissors_boxes(n=6)
        confs = [0.6, 0.7, 0.8, 0.9, 0.95, 0.99]
        for box, c in zip(boxes, confs):
            e.update("cam-1", [person(1), scissors(box, conf=c)], t, *FRAME)
            t += 0.5
        e.update("cam-1", [person(1)], t + 10, *FRAME)  # end it
        rec = e.recent()[0]
        self.assertEqual(rec["activity"], "CUTTING")
        self.assertAlmostEqual(rec["confidence"], sum(confs) / len(confs), places=2)


class CompletedActivities(unittest.TestCase):
    def test_a_completed_activity_has_correct_start_end_and_duration(self):
        e = ActivityEngine(cfg(cutting_min_s=2.0))
        t = 0.0
        boxes = moving_scissors_boxes(n=30)
        for box in boxes:
            e.update("cam-1", [person(1), scissors(box)], t, *FRAME)
            t += 0.5
        end_ts = t
        e.update("cam-1", [person(1)], end_ts + 10, *FRAME)  # activity ends: no scissors, long gap
        rec = e.recent()[0]
        self.assertEqual(rec["track_id"], 1)
        self.assertEqual(rec["activity"], "CUTTING")
        self.assertEqual(rec["zone"], "Cutting Zone")
        self.assertAlmostEqual(rec["start_ts"], 0.0, delta=0.6)
        self.assertGreater(rec["duration_s"], 2.0)

    def test_multiple_tracks_are_independent(self):
        e = ActivityEngine(cfg(cutting_min_s=2.0))
        t = 0.0
        boxes1 = moving_scissors_boxes(base_x=120, n=10)
        boxes2 = moving_scissors_boxes(base_x=700, n=10)
        result = {}
        for b1, b2 in zip(boxes1, boxes2):
            result = e.update("cam-1", [person(1, box=(100, 100, 200, 400)), scissors(b1),
                                        person(2, box=(650, 100, 780, 400)), scissors(b2)], t, *FRAME)
            t += 0.5
        self.assertEqual(result[1], "CUTTING")
        self.assertEqual(result[2], "CUTTING")

    def test_history_is_capped(self):
        e = ActivityEngine(cfg(cutting_min_s=0.5, max_history=3))
        t = 0.0
        for i in range(6):
            for box in moving_scissors_boxes(n=3):
                e.update("cam-1", [person(1), scissors(box)], t, *FRAME)
                t += 0.5
            e.update("cam-1", [person(1)], t + 5, *FRAME)  # end it each time
            t += 5.5
        self.assertEqual(len(e.recent(limit=100)), 3)


class IdleRule(unittest.TestCase):
    def test_a_person_doing_nothing_becomes_idle_after_the_threshold(self):
        e = ActivityEngine(cfg(idle_min_s=5.0))
        t = 0.0
        result = {}
        for _ in range(20):
            result = e.update("cam-1", [person(1, zone=None, zone_activity=None)], t, *FRAME)
            t += 0.5
        self.assertEqual(result[1], "IDLE")

    def test_not_idle_before_the_threshold(self):
        e = ActivityEngine(cfg(idle_min_s=20.0))
        t = 0.0
        result = {}
        for _ in range(10):
            result = e.update("cam-1", [person(1, zone=None, zone_activity=None)], t, *FRAME)
            t += 0.5
        self.assertIsNone(result[1])

    def test_actively_attempting_cutting_is_not_idle_even_before_confirmation(self):
        e = ActivityEngine(cfg(cutting_min_s=10.0, idle_min_s=2.0))  # idle would trigger first if not suppressed
        t = 0.0
        result = {}
        for box in moving_scissors_boxes(n=10):  # 5s of real movement, not yet enough for a 10s cutting rule
            result = e.update("cam-1", [person(1), scissors(box)], t, *FRAME)
            t += 0.5
        self.assertIsNone(result[1])  # not yet CUTTING ...
        self.assertNotEqual(result[1], "IDLE")  # ... and definitely not IDLE either

    def test_idle_ends_when_an_activity_confirms_and_is_recorded(self):
        e = ActivityEngine(cfg(idle_min_s=2.0, cutting_min_s=2.0))
        t = 0.0
        result = {}
        for _ in range(6):  # idle for 3s
            result = e.update("cam-1", [person(1, zone=None, zone_activity=None)], t, *FRAME)
            t += 0.5
        self.assertEqual(result[1], "IDLE")
        for box in moving_scissors_boxes(n=6):  # now they pick up scissors and start cutting
            result = e.update("cam-1", [person(1), scissors(box)], t, *FRAME)
            t += 0.5
        self.assertEqual(result[1], "CUTTING")
        activities = [r["activity"] for r in e.recent()]
        self.assertIn("IDLE", activities)


class DormantRules(unittest.TestCase):
    """MARKING/STITCHING/PIECE_HANDLING are real, correct, tested code - they are simply never
    reachable with the current COCO stand-in (no marking_tool/sewing_machine/fabric_piece class
    exists), so these tests use synthetic classes standing in for the real ones, exactly
    disclosed as such - see rules.py's module docstring."""

    def test_marking_rule_fires_given_its_required_class(self):
        e = ActivityEngine(cfg(marking_min_s=1.0))
        t = 0.0
        result = {}
        tool_boxes = moving_scissors_boxes(n=6)  # reuse the same movement pattern, different class
        for box in tool_boxes:
            tool = FakeDet("marking_tool", box, None, None, None, 0.9)
            result = e.update("cam-1", [person(1, zone="Marking Zone", zone_activity="marking"), tool], t, *FRAME)
            t += 0.5
        self.assertEqual(result[1], "MARKING")

    def test_stitching_rule_needs_no_movement_just_presence_and_duration(self):
        e = ActivityEngine(cfg(stitching_min_s=1.0))
        t = 0.0
        result = {}
        for _ in range(6):
            machine = FakeDet("sewing_machine", (140, 200, 200, 260), None, None, None, 0.9)  # stationary
            result = e.update("cam-1", [person(1, zone="Stitching Zone", zone_activity="stitching"), machine], t, *FRAME)
            t += 0.5
        self.assertEqual(result[1], "STITCHING")

    def test_piece_handling_rule_fires_given_its_required_class(self):
        e = ActivityEngine(cfg(piece_handling_min_s=1.0))
        t = 0.0
        result = {}
        for _ in range(6):
            piece = FakeDet("fabric_piece", (140, 200, 200, 260), None, None, None, 0.9)
            result = e.update("cam-1", [person(1, zone="Collection Area", zone_activity="piece_handling"), piece],
                              t, *FRAME)
            t += 0.5
        self.assertEqual(result[1], "PIECE_HANDLING")

    def test_these_rules_never_fire_from_real_coco_classes_alone(self):
        # a real scissors detection in a "marking" zone must not accidentally satisfy MARKING
        e = ActivityEngine(cfg(marking_min_s=1.0))
        t = 0.0
        result = {}
        for box in moving_scissors_boxes(n=6):
            result = e.update("cam-1", [person(1, zone="Marking Zone", zone_activity="marking"), scissors(box)],
                              t, *FRAME)
            t += 0.5
        self.assertIsNone(result[1])


class EngineDisabled(unittest.TestCase):
    def test_disabled_engine_returns_nothing_and_records_nothing(self):
        e = ActivityEngine(cfg(enabled=False))
        result = e.update("cam-1", [person(1), scissors((120, 200, 150, 230))], 0.0, *FRAME)
        self.assertEqual(result, {})
        self.assertEqual(e.recent(), [])


class SnippingInPlace(unittest.TestCase):
    """Real cutting barely moves the scissors' centre - the hands stay put while the blades open and
    close - so the old "net drift > 3% of the frame diagonal" rule never fired on real footage. The
    numbers here mirror what was measured on real clips: a pair of scissors lying still moved
    1-6 px over a 2 s window, real cutting 11-57 px (box size change included)."""

    def _run(self, boxes, **cfg_kw):
        e = ActivityEngine(cfg(cutting_min_s=2.0, movement_min_fraction=0.0028, **cfg_kw))
        t, result = 0.0, {}
        for box in boxes:
            result = e.update("cam-1", [person(1), scissors(box)], t, *FRAME)
            t += 0.5
        return result

    def test_blades_opening_and_closing_in_place_counts_as_cutting(self):
        # centre drifts ~5 px and the box width swings 30 <-> 40 px: nowhere near 3% (42 px) of drift
        boxes = [(120, 200, 150 + (i % 2) * 10, 230) for i in range(20)]
        self.assertEqual(self._run(boxes)[1], "CUTTING")

    def test_scissors_lying_still_with_detector_jitter_never_counts(self):
        boxes = [(120 + (i % 2), 200, 150 + (i % 2), 230) for i in range(20)]   # +-1 px box jitter
        self.assertIsNone(self._run(boxes)[1])


class ClothContact(unittest.TestCase):
    """Spec section 3's own "Fabric detected" condition: scissors held up in the air, or lying away
    from the cloth, are not cutting. Only applies once the detector has told the engine its model
    can see cloth - the COCO stand-in can't, and must keep working as before."""

    SNIP = [(120, 200, 150 + (i % 2) * 10, 230) for i in range(20)]

    def _fabric(self, box, cls="fabric"):
        return FakeDet(cls, box, None, None, None, 0.9)

    def _run(self, vocabulary, extra, boxes=None):
        e = ActivityEngine(cfg(cutting_min_s=2.0, movement_min_fraction=0.0028))
        if vocabulary is not None:
            e.configure(vocabulary)
        t, result = 0.0, {}
        for box in (boxes or self.SNIP):
            result = e.update("cam-1", [person(1), scissors(box)] + extra, t, *FRAME)
            t += 0.5
        return result

    GARMENT = {"person", "scissors", "fabric", "fabric_piece", "marking_tool"}

    def test_scissors_touching_cloth_count(self):
        self.assertEqual(self._run(self.GARMENT, [self._fabric((80, 231, 400, 330))])[1], "CUTTING")

    def test_a_cut_piece_counts_as_cloth_too(self):
        self.assertEqual(self._run(self.GARMENT, [self._fabric((80, 231, 400, 330), "fabric_piece")])[1], "CUTTING")

    def test_scissors_far_from_the_cloth_do_not_count(self):
        self.assertIsNone(self._run(self.GARMENT, [self._fabric((80, 400, 400, 500))])[1])

    def test_no_cloth_in_the_frame_at_all_does_not_count(self):
        self.assertIsNone(self._run(self.GARMENT, [])[1])

    def test_a_model_that_cannot_see_cloth_skips_the_requirement(self):
        # the COCO stand-in: no cloth class exists, so "no cloth detected" must not block cutting
        self.assertEqual(self._run({"person", "scissors", "table"}, [])[1], "CUTTING")

    def test_the_requirement_can_be_switched_off(self):
        # a small scrap held in the hand is mostly hidden, so the model sees no cloth at all
        e = ActivityEngine(cfg(cutting_min_s=2.0, movement_min_fraction=0.0028, require_cloth_contact=False))
        e.configure(self.GARMENT)
        t, result = 0.0, {}
        for box in self.SNIP:
            result = e.update("cam-1", [person(1), scissors(box)], t, *FRAME)
            t += 0.5
        self.assertEqual(result[1], "CUTTING")

    def test_an_engine_never_told_the_vocabulary_behaves_like_the_stand_in(self):
        self.assertEqual(self._run(None, [])[1], "CUTTING")


class StatusFromVocabulary(unittest.TestCase):
    def test_a_garment_model_makes_more_rules_reachable(self):
        e = ActivityEngine(cfg())
        e.configure({"person", "scissors", "marking_tool", "fabric", "fabric_piece"})
        st = e.status()
        self.assertEqual(st["reachable_now"], ["CUTTING", "MARKING", "PIECE_HANDLING"])
        self.assertEqual(st["dormant"], ["STITCHING"])

    def test_the_coco_stand_in_keeps_only_cutting_reachable(self):
        e = ActivityEngine(cfg())
        e.configure({"person", "scissors", "table"})
        self.assertEqual(e.status()["reachable_now"], ["CUTTING"])


class OnCompleteHook(unittest.TestCase):
    """Phase 6's seam: `ActivityEngine` itself must stay ignorant of events/database - it only
    calls a plain callback with each finished `CompletedActivity`."""

    def test_callback_fires_once_per_completed_activity_with_correct_fields(self):
        seen = []
        e = ActivityEngine(cfg(idle_min_s=1.0), on_complete=seen.append)
        t = 0.0
        # idle for long enough to confirm, then a long gap ends the track (prune) -> 1 completion
        for _ in range(4):
            e.update("cam-1", [person(1)], t, *FRAME)
            t += 0.5
        e.update("cam-1", [], t + 40, *FRAME)  # track goes stale, pruned
        self.assertEqual(len(seen), 1)
        c = seen[0]
        self.assertEqual(c.camera_id, "cam-1")
        self.assertEqual(c.track_id, 1)
        self.assertEqual(c.activity, "IDLE")

    def test_no_callback_configured_does_not_error(self):
        e = ActivityEngine(cfg(idle_min_s=1.0))  # on_complete defaults to None
        t = 0.0
        for _ in range(4):
            e.update("cam-1", [person(1)], t, *FRAME)
            t += 0.5
        e.update("cam-1", [], t + 40, *FRAME)  # must not raise with no listener attached
        self.assertEqual(len(e.recent()), 1)

    def test_callback_fires_for_a_prune_completion_too(self):
        # a track that simply vanishes (not an explicit activity-to-activity transition) must
        # still notify the listener, via _prune's own completion path.
        seen = []
        e = ActivityEngine(cfg(cutting_min_s=1.0), on_complete=seen.append)
        t = 0.0
        for box in moving_scissors_boxes(n=6):
            e.update("cam-1", [person(1), scissors(box)], t, *FRAME)
            t += 0.5
        e.update("cam-2", [], t + 40, *FRAME)  # a different camera's call still ages out track 1
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].activity, "CUTTING")


class Status(unittest.TestCase):
    def test_status_reports_reachable_and_dormant_rules(self):
        e = ActivityEngine(cfg())
        st = e.status()
        self.assertTrue(st["enabled"])
        self.assertEqual(st["reachable_now"], ["CUTTING"])
        self.assertEqual(sorted(st["dormant"]), ["MARKING", "PIECE_HANDLING", "STITCHING"])

    def test_current_reflects_live_state(self):
        e = ActivityEngine(cfg(cutting_min_s=1.0))
        t = 0.0
        for box in moving_scissors_boxes(n=6):
            e.update("cam-1", [person(1), scissors(box)], t, *FRAME)
            t += 0.5
        cur = e.current()
        self.assertEqual(cur[1]["activity"], "CUTTING")
        self.assertEqual(cur[1]["zone"], "Cutting Zone")


class TickCallback(unittest.TestCase):
    """The Odoo bridge's feed: a per-update set of what is confirmed anywhere in view, independent
    of person track ids."""

    def test_tick_reports_confirmed_activities_not_idle_and_every_update(self):
        ticks = []
        engine = ActivityEngine(cfg(), on_tick=lambda cam, ts, acts: ticks.append((ts, acts)))
        boxes = moving_scissors_boxes(n=12)
        for i, b in enumerate(boxes):
            engine.update("cam", [person(1), scissors(b)], ts=i * 0.5, frame_w=1000, frame_h=1000)
        self.assertEqual(len(ticks), len(boxes))                 # one tick per update
        self.assertEqual(ticks[0][1], frozenset())               # nothing confirmed yet
        self.assertIn(frozenset({"CUTTING"}), [a for _, a in ticks])
        self.assertTrue(all("IDLE" not in a for _, a in ticks))

    def test_tick_fires_even_when_nobody_is_in_view(self):
        ticks = []
        engine = ActivityEngine(cfg(), on_tick=lambda cam, ts, acts: ticks.append(acts))
        engine.update("cam", [], ts=1.0, frame_w=1000, frame_h=1000)
        self.assertEqual(ticks, [frozenset()])


class SharedZone(unittest.TestCase):
    """One table where both marking and cutting happen: the zone lists both activities."""

    def run_scene(self, zone_activity, tool_cls):
        engine = ActivityEngine(cfg(marking_min_s=3.0, require_cloth_contact=False))
        boxes = moving_scissors_boxes(n=16)
        confirmed = set()
        for i, b in enumerate(boxes):
            d = FakeDet(tool_cls, b)
            acts = engine.update("cam", [person(1, zone="Table", zone_activity=zone_activity), d],
                                 ts=i * 0.5, frame_w=1000, frame_h=1000)
            confirmed.add(acts.get(1))
        return confirmed

    def test_marking_fires_in_a_marking_and_cutting_zone(self):
        self.assertIn("MARKING", self.run_scene("marking,cutting", "marking_tool"))

    def test_cutting_still_fires_there_too(self):
        self.assertIn("CUTTING", self.run_scene("marking,cutting", "scissors"))

    def test_a_cutting_only_zone_still_never_marks(self):
        self.assertNotIn("MARKING", self.run_scene("cutting", "marking_tool"))

    def test_zone_with_no_activity_never_matches(self):
        self.assertEqual(self.run_scene(None, "marking_tool") - {None}, set())


if __name__ == "__main__":
    unittest.main()
