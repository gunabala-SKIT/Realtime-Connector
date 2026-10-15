"""End-to-end: a real camera feed (a short video built from a real photo) through the real
CameraRunner -> frame_hooks -> ObjectDetector -> API, exactly as main.py wires it."""
import logging
import os
import tempfile
import time
import unittest

import cv2
from fastapi.testclient import TestClient

from garment_cv.backend.api.routes import create_app
from garment_cv.backend.cameras.config import CameraConfig
from garment_cv.backend.cameras.runner import CameraRunner
from garment_cv.backend.detection.config import DetectionConfig
from garment_cv.backend.detection.detector import ObjectDetector
from garment_cv.backend.tracking.config import TrackingConfig

log = logging.getLogger("test")
log.addHandler(logging.NullHandler())

ASSET = os.path.join(os.path.dirname(__file__), "assets", "bus_small.jpg")


def make_person_clip(path: str, seconds: float = 2.0, fps: int = 5) -> None:
    img = cv2.imread(ASSET)
    h, w = img.shape[:2]
    out = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for _ in range(int(seconds * fps)):
        out.write(img)
    out.release()


def cam_cfg(source: str) -> CameraConfig:
    return CameraConfig(camera_id="c1", camera_name="Marking Table", source=source, camera_user="",
                        camera_password="", rtsp_transport="tcp", open_timeout_ms=2000, read_retry_limit=3,
                        reconnect_wait_seconds=0.5, reconnect_max_attempts=2, startup_max_attempts=2,
                        capture_buffer=None, ffmpeg_loglevel=None, stream_max_width=480, jpeg_quality=80)


def det_cfg() -> DetectionConfig:
    return DetectionConfig(enabled=True, model="yolo11n.pt", device="", conf=0.35, iou=0.5, imgsz=320,
                           half=False, detect_interval_s=0.0, stream_max_width=480, jpeg_quality=80)


def track_cfg(**kw) -> TrackingConfig:
    base = dict(enabled=True, tracker="botsort.yaml")
    base.update(kw)
    return TrackingConfig(**base)


def wait_until(fn, timeout=8.0, interval=0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if fn():
            return True
        time.sleep(interval)
    return fn()


class WithDetection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.clip = os.path.join(cls.tmp, "clip.mp4")
        make_person_clip(cls.clip)
        cls.runner = CameraRunner(cam_cfg(cls.clip), log)
        cls.detector = ObjectDetector(det_cfg(), log, tracking=track_cfg())
        cls.app = create_app(cls.runner, detector=cls.detector, start_camera=True)
        cls.client = TestClient(cls.app)
        cls.client.__enter__()
        wait_until(lambda: cls.detector.status()["detections"])
        wait_until(lambda: any(d["track_id"] is not None for d in cls.detector.status()["detections"]))

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def test_detector_started_via_the_lifespan_and_attached_to_the_camera(self):
        self.assertTrue(self.detector.enabled)
        self.assertIn(self.detector.observe, self.runner.frame_hooks)

    def test_detections_endpoint_reports_real_people(self):
        r = self.client.get("/api/detections")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["enabled"])
        self.assertTrue(any(d["class"] == "person" for d in body["detections"]))
        self.assertIn("fabric", body["not_yet_detectable"])
        self.assertIn("sewing_machine", body["not_yet_detectable"])
        self.assertTrue(body["tracking_enabled"])
        self.assertEqual(body["tracker"], "botsort.yaml")
        self.assertTrue(any(d["track_id"] is not None for d in body["detections"]),
                        "no track id came through the real API response")

    def test_annotated_frame_is_a_real_jpeg(self):
        r = self.client.get("/live/frame.jpg")  # the raw stream still works unmodified
        self.assertEqual(r.status_code, 200)
        jpg = self.detector.latest_jpeg()
        self.assertIsNotNone(jpg)
        self.assertEqual(jpg[:2], b"\xff\xd8")

    def test_annotated_mjpeg_route_is_registered_when_a_detector_is_present(self):
        routes = {rt.path for rt in self.app.routes}
        self.assertIn("/live/annotated.mjpg", routes)

    def test_index_page_mentions_detection_and_the_not_yet_detectable_classes(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Detection is on", r.text)
        self.assertIn("sewing_machine", r.text)
        self.assertIn("/live/annotated.mjpg", r.text)


class WithoutDetector(unittest.TestCase):
    def test_no_detector_means_no_detection_routes_phase_1_behaviour_unchanged(self):
        runner = CameraRunner(cam_cfg("D:/nope.mp4"), log)
        app = create_app(runner, detector=None, start_camera=False)
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/detections").status_code, 404)
            self.assertEqual(client.get("/live/annotated.mjpg").status_code, 404)
            r = client.get("/")
            self.assertIn("plain camera picture", r.text)
            self.assertNotIn("/live/annotated.mjpg", r.text)


class DetectionDisabledByConfig(unittest.TestCase):
    def test_annotated_route_exists_but_reports_503(self):
        runner = CameraRunner(cam_cfg("D:/nope.mp4"), log)
        detector = ObjectDetector(det_cfg().__class__(**{**det_cfg().__dict__, "enabled": False}), log)
        app = create_app(runner, detector=detector, start_camera=False)
        with TestClient(app) as client:
            self.assertEqual(client.get("/live/annotated.mjpg").status_code, 503)
            self.assertEqual(client.get("/api/detections").json()["enabled"], False)


if __name__ == "__main__":
    unittest.main()
