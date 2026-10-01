"""End-to-end for Phase 6's actual point: a completed activity from the real `ActivityEngine`
reaches the real `EventStore` through `main.py`'s wiring, with the worker name resolved through
`WorkerStore` - not three isolated unit tests that never prove the pieces fit together.
"""
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from garment_cv.backend.activities.config import ActivityConfig
from garment_cv.backend.activities.engine import ActivityEngine
from garment_cv.backend.database.db import Database
from garment_cv.backend.events.store import EventStore
from garment_cv.backend.main import _record_completed_activity
from garment_cv.backend.workers.store import WorkerStore

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
    base = dict(enabled=True, cutting_min_s=1.0, marking_min_s=3.0, stitching_min_s=5.0,
               piece_handling_min_s=2.0, idle_min_s=1.0, grace_s=1.5, movement_window_s=2.0,
               movement_min_fraction=0.03, attribution_margin=0.3, max_history=200)
    base.update(kw)
    return ActivityConfig(**base)


def person(track_id, box=(100, 100, 200, 400), zone=None, zone_activity=None) -> FakeDet:
    return FakeDet(cls="person", box=box, track_id=track_id, zone=zone, zone_activity=zone_activity)


class Pipeline(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmpdir.name) / "pipeline.db")
        self.events = EventStore(self.db)
        self.workers = WorkerStore(self.db)
        self.engine = ActivityEngine(cfg(),
                                     on_complete=lambda c: _record_completed_activity(self.events, self.workers, c))

    def tearDown(self):
        self.db.close()
        self.tmpdir.cleanup()

    def test_a_completed_idle_activity_lands_in_the_database_with_the_fallback_worker_name(self):
        t = 0.0
        for _ in range(4):
            self.engine.update("cam-1", [person(7)], t, *FRAME)
            t += 0.5
        self.engine.update("cam-1", [], t + 40, *FRAME)  # track goes stale -> prune -> completes

        events = self.events.list()
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev.stage, "IDLE")
        self.assertEqual(ev.camera_id, "cam-1")
        self.assertEqual(ev.track_id, 7)
        self.assertEqual(ev.worker, "track-7")  # no manual assignment made

    def test_an_assigned_worker_name_is_used_instead_of_the_fallback(self):
        self.workers.assign(7, "Priya")
        t = 0.0
        for _ in range(4):
            self.engine.update("cam-1", [person(7)], t, *FRAME)
            t += 0.5
        self.engine.update("cam-1", [], t + 40, *FRAME)

        events = self.events.list()
        self.assertEqual(events[0].worker, "Priya")


if __name__ == "__main__":
    unittest.main()
