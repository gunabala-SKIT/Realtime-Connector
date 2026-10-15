"""API-level tests for Phase 6 (events, workers, analytics) and Phase 7 (the dashboard route) -
plain CRUD/query tests without any camera or model needed, since the event/worker stores don't
depend on either."""
import logging
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from garment_cv.backend.api.routes import create_app
from garment_cv.backend.cameras.runner import CameraRunner
from garment_cv.backend.database.db import Database
from garment_cv.backend.events.store import EventStore
from garment_cv.backend.workers.store import WorkerStore

from .test_api_detection import cam_cfg

log = logging.getLogger("test")
log.addHandler(logging.NullHandler())


def new_app():
    tmpdir = tempfile.mkdtemp()
    db = Database(Path(tmpdir) / "test.db")
    event_store = EventStore(db)
    worker_store = WorkerStore(db)
    runner = CameraRunner(cam_cfg("D:/nope.mp4"), log)
    app = create_app(runner, detector=None, zone_store=None, start_camera=False,
                     event_store=event_store, worker_store=worker_store)
    return app, event_store, worker_store


class EventsApi(unittest.TestCase):
    def setUp(self):
        self.app, self.events, self.workers = new_app()
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(lambda: self.client.__exit__(None, None, None))

    def test_no_events_yet_returns_empty_list(self):
        self.assertEqual(self.client.get("/api/events").json(), [])

    def test_recorded_events_come_back_through_the_api(self):
        self.events.record(camera_id="cam-1", track_id=1, worker="track-1", stage="CUTTING",
                           zone="Cutting Zone", start_ts=0.0, end_ts=5.0, duration_s=5.0, confidence=0.9)
        r = self.client.get("/api/events")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["stage"], "CUTTING")
        self.assertEqual(body[0]["duration_s"], 5.0)

    def test_filters_are_wired_through_query_params(self):
        self.events.record(camera_id="cam-1", track_id=1, worker="a", stage="CUTTING", zone="Z",
                           start_ts=0.0, end_ts=5.0, duration_s=5.0, confidence=0.9)
        self.events.record(camera_id="cam-1", track_id=2, worker="b", stage="IDLE", zone=None,
                           start_ts=0.0, end_ts=5.0, duration_s=5.0, confidence=1.0)
        self.assertEqual(len(self.client.get("/api/events", params={"stage": "IDLE"}).json()), 1)
        self.assertEqual(len(self.client.get("/api/events", params={"worker": "a"}).json()), 1)

    def test_no_event_store_means_no_event_routes(self):
        runner = CameraRunner(cam_cfg("D:/nope.mp4"), log)
        app = create_app(runner, detector=None, zone_store=None, start_camera=False, event_store=None)
        with TestClient(app) as c:
            self.assertEqual(c.get("/api/events").status_code, 404)


class AnalyticsApi(unittest.TestCase):
    def setUp(self):
        self.app, self.events, self.workers = new_app()
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(lambda: self.client.__exit__(None, None, None))

    def test_empty_store_gives_empty_but_valid_analytics(self):
        body = self.client.get("/api/analytics").json()
        self.assertEqual(body["stage_summary"], {})
        self.assertEqual(body["bottlenecks"], [])

    def test_stage_summary_and_worker_times_reflect_recorded_events(self):
        self.events.record(camera_id="cam-1", track_id=1, worker="alice", stage="CUTTING", zone="Z",
                           start_ts=0.0, end_ts=10.0, duration_s=10.0, confidence=0.9)
        self.events.record(camera_id="cam-1", track_id=1, worker="alice", stage="IDLE", zone=None,
                           start_ts=10.0, end_ts=30.0, duration_s=20.0, confidence=1.0)
        body = self.client.get("/api/analytics").json()
        self.assertEqual(body["stage_summary"]["CUTTING"]["count"], 1)
        self.assertAlmostEqual(body["worker_active_seconds"]["alice"], 10.0)
        self.assertAlmostEqual(body["worker_idle_seconds"]["alice"], 20.0)

    def test_bottleneck_flagged_only_when_baseline_configured_and_exceeded(self):
        import garment_cv.backend.config as base_config
        saved = base_config._ENV_LOADED
        base_config._ENV_LOADED = True
        os.environ["GARMENT_BASELINE_CUTTING_S"] = "5"
        try:
            self.events.record(camera_id="cam-1", track_id=1, worker="alice", stage="CUTTING", zone="Z",
                               start_ts=0.0, end_ts=20.0, duration_s=20.0, confidence=0.9)
            body = self.client.get("/api/analytics").json()
            cutting = next(b for b in body["bottlenecks"] if b["stage"] == "CUTTING")
            self.assertEqual(cutting["status"], "Needs Investigation")
        finally:
            base_config._ENV_LOADED = saved
            os.environ.pop("GARMENT_BASELINE_CUTTING_S", None)


class WorkersApi(unittest.TestCase):
    def setUp(self):
        self.app, self.events, self.workers = new_app()
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(lambda: self.client.__exit__(None, None, None))

    def test_assign_then_list(self):
        r = self.client.post("/api/workers", json={"track_id": 3, "name": "Alice"})
        self.assertEqual(r.status_code, 200)
        r = self.client.get("/api/workers")
        self.assertEqual(r.json(), [{"track_id": 3, "name": "Alice", "assigned_at": r.json()[0]["assigned_at"]}])

    def test_empty_name_is_400(self):
        r = self.client.post("/api/workers", json={"track_id": 3, "name": "   "})
        self.assertEqual(r.status_code, 400)

    def test_unassign_removes_it(self):
        self.client.post("/api/workers", json={"track_id": 3, "name": "Alice"})
        r = self.client.delete("/api/workers/3")
        self.assertTrue(r.json()["ok"])
        self.assertEqual(self.client.get("/api/workers").json(), [])


class DashboardPage(unittest.TestCase):
    def test_dashboard_renders_when_events_and_activities_are_wired(self):
        app, _events, _workers = new_app()
        # the dashboard route only appears when BOTH event_store and activity_engine are present -
        # add a bare ActivityEngine too, since this test only cares about the page rendering
        from garment_cv.backend.activities.config import ActivityConfig
        from garment_cv.backend.activities.engine import ActivityEngine
        runner = CameraRunner(cam_cfg("D:/nope.mp4"), log)
        tmpdir = tempfile.mkdtemp()
        db = Database(Path(tmpdir) / "test.db")
        event_store = EventStore(db)
        worker_store = WorkerStore(db)
        engine = ActivityEngine(ActivityConfig(enabled=True, cutting_min_s=3, marking_min_s=3,
                                               stitching_min_s=5, piece_handling_min_s=2, idle_min_s=10,
                                               grace_s=1.5, movement_window_s=2, movement_min_fraction=0.03,
                                               attribution_margin=0.3, max_history=200))
        app = create_app(runner, detector=None, zone_store=None, start_camera=False,
                         activity_engine=engine, event_store=event_store, worker_store=worker_store)
        with TestClient(app) as c:
            r = c.get("/dashboard")
            self.assertEqual(r.status_code, 200)
            self.assertIn("Production timeline", r.text)
            self.assertIn("Live monitoring", r.text)

    def test_no_dashboard_route_without_event_store(self):
        runner = CameraRunner(cam_cfg("D:/nope.mp4"), log)
        app = create_app(runner, detector=None, zone_store=None, start_camera=False)
        with TestClient(app) as c:
            self.assertEqual(c.get("/dashboard").status_code, 404)


if __name__ == "__main__":
    unittest.main()
