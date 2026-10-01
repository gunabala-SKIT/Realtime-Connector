"""Phase 6's actual deliverable: converting a completed activity into a persisted production
event with Start/End/Duration/Worker/Camera/Zone/Confidence (spec section 12), plus the
aggregate queries Phase 7's dashboard reads from (stage averages, idle vs active time).
"""
import tempfile
import unittest
from pathlib import Path

from garment_cv.backend.database.db import Database
from garment_cv.backend.events.store import EventStore


class RecordAndList(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmpdir.name) / "events.db")
        self.store = EventStore(self.db)

    def tearDown(self):
        self.db.close()
        self.tmpdir.cleanup()

    def test_record_returns_the_event_with_an_id(self):
        ev = self.store.record(camera_id="cam-1", track_id=3, worker="track-3", stage="CUTTING",
                               zone="Cutting Zone", start_ts=10.0, end_ts=15.0, duration_s=5.0,
                               confidence=0.91)
        self.assertIsInstance(ev.id, int)
        self.assertEqual(ev.stage, "CUTTING")
        self.assertEqual(ev.worker, "track-3")

    def test_list_returns_most_recent_first(self):
        self.store.record(camera_id="cam-1", track_id=1, worker="track-1", stage="IDLE", zone=None,
                          start_ts=0.0, end_ts=5.0, duration_s=5.0, confidence=1.0)
        self.store.record(camera_id="cam-1", track_id=1, worker="track-1", stage="CUTTING",
                          zone="Cutting Zone", start_ts=10.0, end_ts=15.0, duration_s=5.0, confidence=0.9)
        events = self.store.list()
        self.assertEqual([e.stage for e in events], ["CUTTING", "IDLE"])

    def test_list_filters_by_stage_camera_and_worker(self):
        self.store.record(camera_id="cam-1", track_id=1, worker="alice", stage="CUTTING", zone="Z",
                          start_ts=0.0, end_ts=5.0, duration_s=5.0, confidence=0.9)
        self.store.record(camera_id="cam-2", track_id=2, worker="bob", stage="IDLE", zone=None,
                          start_ts=0.0, end_ts=5.0, duration_s=5.0, confidence=1.0)
        self.assertEqual(len(self.store.list(stage="CUTTING")), 1)
        self.assertEqual(len(self.store.list(camera_id="cam-2")), 1)
        self.assertEqual(len(self.store.list(worker="alice")), 1)
        self.assertEqual(len(self.store.list(worker="nobody")), 0)

    def test_list_respects_since_ts(self):
        self.store.record(camera_id="cam-1", track_id=1, worker="a", stage="IDLE", zone=None,
                          start_ts=0.0, end_ts=5.0, duration_s=5.0, confidence=1.0)
        self.store.record(camera_id="cam-1", track_id=1, worker="a", stage="IDLE", zone=None,
                          start_ts=100.0, end_ts=105.0, duration_s=5.0, confidence=1.0)
        self.assertEqual(len(self.store.list(since_ts=50.0)), 1)


class Aggregates(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmpdir.name) / "events.db")
        self.store = EventStore(self.db)
        # Two CUTTING events (5s, 15s -> avg 10s) and one IDLE (20s), same worker.
        self.store.record(camera_id="cam-1", track_id=1, worker="alice", stage="CUTTING", zone="Z",
                          start_ts=0.0, end_ts=5.0, duration_s=5.0, confidence=0.9)
        self.store.record(camera_id="cam-1", track_id=1, worker="alice", stage="CUTTING", zone="Z",
                          start_ts=10.0, end_ts=25.0, duration_s=15.0, confidence=0.8)
        self.store.record(camera_id="cam-1", track_id=1, worker="alice", stage="IDLE", zone=None,
                          start_ts=30.0, end_ts=50.0, duration_s=20.0, confidence=1.0)

    def tearDown(self):
        self.db.close()
        self.tmpdir.cleanup()

    def test_stage_summary_counts_and_averages(self):
        summary = self.store.stage_summary()
        self.assertEqual(summary["CUTTING"]["count"], 2)
        self.assertAlmostEqual(summary["CUTTING"]["avg_s"], 10.0)
        self.assertAlmostEqual(summary["CUTTING"]["total_s"], 20.0)
        self.assertEqual(summary["IDLE"]["count"], 1)

    def test_worker_active_seconds_excludes_idle(self):
        active = self.store.worker_active_seconds()
        self.assertAlmostEqual(active["alice"], 20.0)  # 5 + 15, not the 20s IDLE

    def test_worker_idle_seconds_only_idle(self):
        idle = self.store.worker_idle_seconds()
        self.assertAlmostEqual(idle["alice"], 20.0)


if __name__ == "__main__":
    unittest.main()
