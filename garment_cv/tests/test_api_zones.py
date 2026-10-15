"""API-level zone tests: plain CRUD without any model needed, plus one end-to-end check that a
real detected person's zone comes through `/api/detections` correctly."""
import logging
import os
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from garment_cv.backend.api.routes import create_app
from garment_cv.backend.cameras.config import CameraConfig
from garment_cv.backend.cameras.runner import CameraRunner
from garment_cv.backend.detection.config import DetectionConfig
from garment_cv.backend.detection.detector import ObjectDetector
from garment_cv.backend.tracking.config import TrackingConfig
from garment_cv.backend.zones.resolver import ZoneResolver
from garment_cv.backend.zones.store import ZoneStore

from .test_api_detection import cam_cfg, make_person_clip

log = logging.getLogger("test")
log.addHandler(logging.NullHandler())

SQUARE = [[0.0, 0.2], [0.5, 0.2], [0.5, 0.9], [0.0, 0.9]]   # left half of the frame, roughly where
                                                             # the people stand in bus_small.jpg


def new_store() -> ZoneStore:
    return ZoneStore(os.path.join(tempfile.mkdtemp(), "zones.json"))


def wait_until(fn, timeout=8.0, interval=0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if fn():
            return True
        time.sleep(interval)
    return fn()


class ZoneCrudApi(unittest.TestCase):
    """No camera or model needed for pure CRUD - a disconnected camera is enough."""

    def setUp(self):
        self.store = new_store()
        runner = CameraRunner(cam_cfg("D:/nope.mp4"), log)
        app = create_app(runner, detector=None, zone_store=self.store, start_camera=False)
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(lambda: self.client.__exit__(None, None, None))

    def test_add_list_update_delete(self):
        r = self.client.post("/api/zones", json={"camera_id": "cam-1", "name": "Cutting Zone", "points": SQUARE})
        self.assertEqual(r.status_code, 200)
        zone = r.json()
        self.assertEqual(zone["name"], "Cutting Zone")

        r = self.client.get("/api/zones", params={"camera_id": "cam-1"})
        self.assertEqual([z["name"] for z in r.json()], ["Cutting Zone"])

        r = self.client.patch(f"/api/zones/{zone['id']}", json={"name": "Cutting Area"})
        self.assertEqual(r.json()["name"], "Cutting Area")

        r = self.client.delete(f"/api/zones/{zone['id']}")
        self.assertTrue(r.json()["ok"])
        self.assertEqual(self.client.get("/api/zones").json(), [])

    def test_validation_errors_are_400_with_a_message(self):
        r = self.client.post("/api/zones", json={"camera_id": "cam-1", "name": "X", "points": [[0, 0], [1, 1]]})
        self.assertEqual(r.status_code, 422)  # pydantic's own min_length catches this before the store does
        r = self.client.post("/api/zones", json={"camera_id": "cam-1", "name": "X", "points": [[2, 0], [0, 1], [1, 1]]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("between 0 and 1", r.json()["detail"])

    def test_duplicate_name_rejected(self):
        self.client.post("/api/zones", json={"camera_id": "cam-1", "name": "A", "points": SQUARE})
        r = self.client.post("/api/zones", json={"camera_id": "cam-1", "name": "A", "points": SQUARE})
        self.assertEqual(r.status_code, 400)

    def test_update_or_delete_unknown_zone_is_400(self):
        self.assertEqual(self.client.patch("/api/zones/999", json={"name": "X"}).status_code, 400)
        self.assertEqual(self.client.delete("/api/zones/999").status_code, 400)

    def test_no_zone_store_means_no_zone_routes(self):
        runner = CameraRunner(cam_cfg("D:/nope.mp4"), log)
        app = create_app(runner, detector=None, zone_store=None, start_camera=False)
        with TestClient(app) as c:
            self.assertEqual(c.get("/api/zones").status_code, 404)
            self.assertEqual(c.get("/zones").status_code, 404)

    def test_zones_page_renders_and_names_the_camera(self):
        r = self.client.get("/zones")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Marking Table", r.text)  # cam_cfg()'s camera_name
        self.assertIn("/live/frame.jpg", r.text)


class RealZoneMembership(unittest.TestCase):
    """The one end-to-end check: a real detected person, in a real configured zone, reported
    correctly through the real API - everything upstream (model, tracker) is exactly as it runs
    in production, only the zone's exact shape is picked to match the test fixture photo."""

    @classmethod
    def setUpClass(cls):
        cls.store = new_store()
        cls.store.add_zone("c1", "Cutting Zone", SQUARE)
        cls.tmp = tempfile.mkdtemp()
        cls.clip = os.path.join(cls.tmp, "clip.mp4")
        make_person_clip(cls.clip)
        cls.runner = CameraRunner(cam_cfg(cls.clip), log)
        det_cfg = DetectionConfig(enabled=True, model="yolo11n.pt", device="", conf=0.35, iou=0.5, imgsz=320,
                                  half=False, detect_interval_s=0.0, stream_max_width=480, jpeg_quality=80)
        cls.detector = ObjectDetector(det_cfg, log, tracking=TrackingConfig(enabled=True, tracker="botsort.yaml"),
                                      zones=ZoneResolver(cls.store))
        cls.app = create_app(cls.runner, detector=cls.detector, zone_store=cls.store, start_camera=True)
        cls.client = TestClient(cls.app)
        cls.client.__enter__()
        wait_until(lambda: cls.detector.status()["detections"])

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def test_at_least_one_real_person_is_placed_in_the_configured_zone(self):
        body = self.client.get("/api/detections").json()
        people = [d for d in body["detections"] if d["class"] == "person"]
        self.assertTrue(people)
        self.assertTrue(any(p["zone"] == "Cutting Zone" for p in people),
                        f"no person resolved into the configured zone: {people}")

    def test_index_page_shows_zone_count_and_a_link(self):
        r = self.client.get("/")
        self.assertRegex(r.text, r"\d+ zones? configured")
        self.assertIn("/zones", r.text)


if __name__ == "__main__":
    unittest.main()
