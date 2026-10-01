import logging
import os
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from garment_cv.backend.api.routes import create_app, mjpeg_chunks
from garment_cv.backend.cameras.config import CameraConfig
from garment_cv.backend.cameras.runner import CameraRunner

from .make_test_clip import make_clip

log = logging.getLogger("test")
log.addHandler(logging.NullHandler())


def cfg(source: str, **kw) -> CameraConfig:
    base = dict(camera_id="c1", camera_name="Test Camera", source=source, camera_user="", camera_password="",
               rtsp_transport="tcp", open_timeout_ms=2000, read_retry_limit=3, reconnect_wait_seconds=0.05,
               reconnect_max_attempts=2, startup_max_attempts=2, capture_buffer=None, ffmpeg_loglevel=None,
               stream_max_width=200, jpeg_quality=80)
    base.update(kw)
    return CameraConfig(**base)


def wait_until(fn, timeout=3.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if fn():
            return True
        time.sleep(interval)
    return fn()


class LiveCamera(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.clip = os.path.join(cls.tmp, "clip.mp4")
        cls.meta = make_clip(cls.clip, fps=10, duration_s=3.0)
        cls.runner = CameraRunner(cfg(cls.clip), log)
        cls.app = create_app(cls.runner, start_camera=True)
        cls.client = TestClient(cls.app)
        cls.client.__enter__()  # runs the lifespan: starts the camera
        wait_until(lambda: cls.runner.latest_jpeg() is not None)

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)  # runs the lifespan shutdown: stops the camera

    def test_status_endpoint(self):
        # `connected` polls rather than reads once: under heavy system load elsewhere in the
        # test process (this suite now loads several real YOLO models across other test files),
        # this small file source's pacing can miss enough reads in a row to trip a momentary
        # reconnect - the camera itself recovers in well under a second either way, so the test
        # should tolerate that the same way a real client checking status would, not fail on a
        # single unlucky sample. Confirmed independently reliable with `python -m unittest
        # garment_cv.tests.test_api` alone, where there's no such contention.
        self.assertTrue(wait_until(lambda: self.client.get("/api/status").json()["connected"], timeout=5.0))
        st = self.client.get("/api/status").json()
        self.assertEqual(st["camera_name"], "Test Camera")
        self.assertEqual(st["resolution"], f"{self.meta['width']}x{self.meta['height']}")
        self.assertGreater(st["frames_received"], 0)

    def test_index_page_shows_status(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Test Camera", r.text)
        self.assertIn("connected", r.text)

    def test_single_frame_jpeg(self):
        r = self.client.get("/live/frame.jpg")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "image/jpeg")
        self.assertEqual(r.content[:2], b"\xff\xd8")

    def test_live_mjpg_route_is_registered_and_advertises_the_right_content_type(self):
        # Only the route wiring, not the (infinite) body: driving a real streaming response
        # through FastAPI's TestClient hung indefinitely in practice (see mjpeg_chunks'
        # docstring) - the body itself is covered by test_mjpeg_generator_yields_real_frames
        # below (a plain-Python test) and by a real server smoke test, see the README.
        routes = {r.path: r for r in self.app.routes}
        self.assertIn("/live.mjpg", routes)
        self.assertEqual(routes["/live.mjpg"].methods, {"GET"})

    def test_mjpeg_generator_yields_real_frames(self):
        gen = mjpeg_chunks(self.runner, poll_interval=0.01)
        chunk = next(gen)
        self.assertIn(b"--frame\r\nContent-Type: image/jpeg\r\n", chunk)
        self.assertIn(b"\xff\xd8", chunk)                     # a real JPEG, not a placeholder
        chunk2 = next(gen)                                    # the stream keeps producing frames
        self.assertIn(b"\xff\xd8", chunk2)
        gen.close()

    def test_mjpeg_generator_stops_after_the_idle_timeout(self):
        class NeverReady:
            def latest_jpeg(self):
                return None
        started = time.monotonic()
        with self.assertRaises(StopIteration):
            next(mjpeg_chunks(NeverReady(), idle_timeout=0.05, poll_interval=0.01))
        self.assertLess(time.monotonic() - started, 2.0)

    def test_manual_reconnect_endpoint(self):
        r = self.client.post("/api/reconnect")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        self.assertTrue(wait_until(lambda: self.client.get("/api/status").json()["connected"]))


class NoFrameYet(unittest.TestCase):
    def test_frame_endpoint_is_503_before_the_first_frame(self):
        runner = CameraRunner(cfg("D:/nope.mp4"), log)
        app = create_app(runner, start_camera=False)  # camera never started
        with TestClient(app) as client:
            self.assertEqual(client.get("/live/frame.jpg").status_code, 503)
            st = client.get("/api/status").json()
            self.assertFalse(st["connected"])
            self.assertFalse(st["running"])


if __name__ == "__main__":
    unittest.main()
