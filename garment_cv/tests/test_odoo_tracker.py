"""The Odoo work-order tracker: hand-built observation sequences, no camera or Odoo.

The scenarios mirror what the user asked for: marking starts the first operation, cutting hands
off to the second, and - the important one - picking up / putting down scissors must NOT finish
anything.
"""
import unittest

from garment_cv.backend.odoo_bridge.tracker import (ACTIVE, FINISHED, PENDING, FlowConfig, OperationTracker, Step)

MARK, CUT = frozenset({"MARKING"}), frozenset({"CUTTING"})
NONE = frozenset()
STEPS = [Step("Marking", "MARKING"), Step("Cutting", "CUTTING")]
CFG = FlowConfig(start_hold_s=2, handoff_hold_s=3, min_active_s=10, quiet_s=45, hold_grace_s=3, max_gap_s=5)


class Feed:
    """Drives a tracker at a fixed frame period and collects every decision."""

    def __init__(self, tracker, step_s=0.5, t0=1000.0):
        self.t, self.tracker, self.step_s, self.decisions = t0, tracker, step_s, []

    def run(self, seconds, activities=NONE, healthy=True):
        for _ in range(int(round(seconds / self.step_s))):
            self.t += self.step_s
            self.decisions += self.tracker.observe(self.t, activities, healthy)
        return self

    def kinds(self):
        return [(d.kind, d.step) for d in self.decisions]


def make(cfg=CFG):
    tr = OperationTracker(STEPS, cfg)
    return tr, Feed(tr)


class StartAndHandoff(unittest.TestCase):
    def test_marking_must_be_sustained_before_the_first_step_starts(self):
        tr, f = make()
        f.run(1.0, MARK)
        self.assertEqual(f.kinds(), [])            # 1 s of marking is not enough
        f.run(2.0, MARK)
        self.assertEqual(f.kinds(), [("START", 0)])
        self.assertEqual(tr.states, [ACTIVE, PENDING])

    def test_a_flicker_of_marking_does_not_start_anything(self):
        tr, f = make()
        for _ in range(10):
            f.run(1.0, MARK).run(5.0, NONE)         # 1 s bursts separated by long gaps
        self.assertEqual(f.kinds(), [])

    def test_marking_survives_a_short_dropout(self):
        tr, f = make()
        f.run(1.0, MARK).run(1.5, NONE).run(1.5, MARK)   # 1.5 s gap < grace 3 s
        self.assertEqual(f.kinds(), [("START", 0)])

    def test_cutting_hands_off_from_marking(self):
        tr, f = make()
        f.run(4, MARK)
        f.run(2, CUT)
        self.assertEqual(f.kinds(), [("START", 0)])        # 2 s of cutting: not yet
        f.run(2, CUT)
        self.assertEqual(f.kinds(), [("START", 0), ("FINISH", 0), ("START", 1)])
        self.assertEqual(tr.states, [FINISHED, ACTIVE])

    def test_picking_up_scissors_while_marking_changes_nothing(self):
        """Scissors lifted but not cutting never produce a CUTTING observation - the activity
        engine needs cloth contact + movement - so the tracker only ever sees MARKING/nothing."""
        tr, f = make()
        f.run(4, MARK).run(20, NONE).run(4, MARK).run(30, NONE)
        self.assertEqual(f.kinds(), [("START", 0)])
        self.assertEqual(tr.states, [ACTIVE, PENDING])     # marking stays open: it's not the last step

    def test_cutting_before_any_marking_is_flagged_not_acted_on_as_a_skip(self):
        tr, f = make()
        f.run(10, CUT)
        # step 0 is still pending and CUTTING is not its activity: nothing starts, a note is raised
        self.assertEqual(f.kinds(), [])
        self.assertIn("CUTTING", tr.unexpected)


class FinishingTheLastStep(unittest.TestCase):
    def started_cutting(self):
        tr, f = make()
        f.run(4, MARK).run(4, CUT)
        self.assertEqual(tr.states, [FINISHED, ACTIVE])
        return tr, f

    def test_finishes_after_enough_cutting_then_quiet(self):
        tr, f = self.started_cutting()
        f.run(12, CUT)
        f.run(40, NONE)
        self.assertNotIn(("FINISH", 1), f.kinds())          # 40 s quiet < 45 s
        f.run(6, NONE)
        self.assertEqual(f.kinds()[-1], ("FINISH", 1))
        self.assertEqual(tr.states, [FINISHED, FINISHED])

    def test_picking_up_and_putting_down_scissors_does_not_finish_cutting(self):
        """The user's own example. Pauses shorter than the quiet period, however many, never finish."""
        tr, f = self.started_cutting()
        f.run(12, CUT)
        for _ in range(6):                                   # six scissors pick-ups across ~4 minutes
            f.run(30, NONE).run(2, CUT)
        self.assertNotIn(("FINISH", 1), f.kinds())

    def test_resuming_cutting_cancels_the_countdown_and_keeps_the_same_work_order(self):
        tr, f = self.started_cutting()
        f.run(12, CUT).run(30, NONE)
        self.assertIsNotNone(tr.finishing_in_s())
        f.run(3, CUT)
        self.assertIsNone(tr.finishing_in_s())
        self.assertEqual(tr.states, [FINISHED, ACTIVE])
        self.assertEqual(f.kinds().count(("START", 1)), 1)   # never restarted

    def test_a_short_cut_never_finishes_however_long_the_quiet(self):
        tr, f = self.started_cutting()
        f.run(4, CUT)                                        # < min_active_s of cutting in total
        f.run(600, NONE)
        self.assertNotIn(("FINISH", 1), f.kinds())

    def test_camera_outage_is_not_quiet(self):
        tr, f = self.started_cutting()
        f.run(12, CUT)
        f.run(300, NONE, healthy=False)                      # camera down for 5 minutes
        self.assertNotIn(("FINISH", 1), f.kinds())
        self.assertEqual(tr.status()["quiet_s"], 0.0)

    def test_a_gap_in_observations_is_not_quiet_either(self):
        tr, f = self.started_cutting()
        f.run(12, CUT)
        f.t += 600                                           # nothing arrived for 10 minutes (stalled app)
        f.run(1, NONE)
        self.assertNotIn(("FINISH", 1), f.kinds())

    def test_hold_blocks_auto_finish_until_released(self):
        tr, f = self.started_cutting()
        tr.held = True
        f.run(12, CUT).run(120, NONE)
        self.assertNotIn(("FINISH", 1), f.kinds())
        tr.held = False
        f.run(1, NONE)
        self.assertEqual(f.kinds()[-1], ("FINISH", 1))

    def test_no_decision_is_ever_repeated(self):
        tr, f = self.started_cutting()
        f.run(12, CUT).run(200, NONE)
        self.assertEqual(f.kinds(), [("START", 0), ("FINISH", 0), ("START", 1), ("FINISH", 1)])

    def test_finishing_in_counts_down(self):
        tr, f = self.started_cutting()
        f.run(12, CUT).run(10, NONE)
        self.assertAlmostEqual(tr.finishing_in_s(), 35, delta=1)


class DefaultFinishWait(unittest.TestCase):
    """Out of the box the last operation closes 15 seconds after the work stops - idle, walking
    away or anything else that is not cutting."""

    def started_cutting(self):
        tr = OperationTracker(STEPS)                      # default FlowConfig, not the 45 s test one
        f = Feed(tr)
        f.run(5, MARK).run(4, CUT).run(12, CUT)
        self.assertEqual(tr.states, [FINISHED, ACTIVE])
        return tr, f

    def test_the_default_wait_is_fifteen_seconds(self):
        self.assertEqual(FlowConfig().quiet_s, 15.0)

    def test_finishes_within_fifteen_seconds_of_the_last_cut(self):
        tr, f = self.started_cutting()
        f.run(14, NONE)
        self.assertNotIn(("FINISH", 1), f.kinds())
        f.run(2, NONE)
        self.assertEqual(f.kinds()[-1], ("FINISH", 1))

    def test_a_short_pause_under_fifteen_seconds_still_does_not_finish(self):
        tr, f = self.started_cutting()
        f.run(10, NONE).run(3, CUT).run(10, NONE).run(3, CUT)      # picking up and putting down, cutting again
        self.assertNotIn(("FINISH", 1), f.kinds())

    def test_the_countdown_shown_to_the_operator_never_exceeds_fifteen_seconds(self):
        tr, f = self.started_cutting()
        f.run(1, NONE)
        self.assertLessEqual(tr.finishing_in_s(), 15.0)


class SyncWithOdoo(unittest.TestCase):
    def test_sync_adopts_odoo_state_and_resets_evidence(self):
        tr, f = make()
        f.run(4, MARK).run(4, CUT)
        self.assertTrue(tr.sync_states([FINISHED, PENDING]))   # someone reset the cutting WO in Odoo
        self.assertEqual(tr.states, [FINISHED, PENDING])
        self.assertFalse(tr.sync_states([FINISHED, PENDING]))

    def test_restart_midway_continues_from_the_restored_state(self):
        tr, f = make()
        f.run(4, MARK).run(4, CUT).run(12, CUT)
        snap = tr.snapshot()
        tr2, f2 = make()
        tr2.restore(snap)
        f2.run(60, NONE)
        self.assertEqual(f2.kinds(), [("FINISH", 1)])

    def test_status_reports_steps_for_the_dashboard(self):
        tr, f = make()
        f.run(4, MARK)
        st = tr.status()
        self.assertEqual([s["state"] for s in st["steps"]], [ACTIVE, PENDING])
        self.assertEqual(st["current"], 0)

    def test_clock_going_backwards_is_ignored(self):
        tr, f = make()
        f.run(4, MARK)
        self.assertEqual(tr.observe(f.t - 100, MARK), [])

    def test_empty_flow_is_rejected(self):
        with self.assertRaises(ValueError):
            OperationTracker([])


if __name__ == "__main__":
    unittest.main()
