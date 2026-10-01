import logging
import os
import tempfile
import time
import unittest

from garment_cv.backend.cameras.config import CameraConfig
from garment_cv.backend.cameras.runner import CameraRunner

from .make_test_clip import make_clip

log = logging.getLogger("test")
log.addHandler(logging.NullHandler())


def cfg(source: str, **kw) -> CameraConfig:
    base = dict(camera_id="c1", camera_name="Camera 1", source=source, camera_user="", camera_password="",
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


class Basic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.clip = os.path.join(cls.tmp, "clip.mp4")
        cls.meta = make_clip(cls.clip, fps=10, duration_s=3.0)

    def test_runs_in_background_and_serves_frames(self):
        r = CameraRunner(cfg(self.clip), log)
        self.assertTrue(r.start())
        try:
            self.assertTrue(wait_until(lambda: r.latest_jpeg() is not None))
            jpg = r.latest_jpeg()
            self.assertGreater(len(jpg), 100)
            self.assertEqual(jpg[:2], b"\xff\xd8")  # JPEG magic bytes
            frame, ts = r.latest_frame()
            self.assertIsNotNone(frame)
            self.assertGreater(ts, 0)
            st = r.status()
            self.assertTrue(st["running"])
            self.assertTrue(st["connected"])
            self.assertEqual(st["camera_id"], "c1")
        finally:
            r.stop()

    def test_frame_hooks_are_called_with_expected_arguments(self):
        r = CameraRunner(cfg(self.clip), log)
        seen = []
        r.frame_hooks.append(lambda cam_id, frame, ts: seen.append((cam_id, frame.shape, ts)))
        r.start()
        try:
            self.assertTrue(wait_until(lambda: len(seen) >= 3))
            cam_id, shape, ts = seen[0]
            self.assertEqual(cam_id, "c1")
            self.assertEqual(shape[:2], (self.meta["height"], self.meta["width"]))
            self.assertGreater(ts, 0)
        finally:
            r.stop()

    def test_a_hook_that_raises_does_not_stop_the_camera(self):
        r = CameraRunner(cfg(self.clip), log)
        calls = []

        def bad_hook(cam_id, frame, ts):
            calls.append(1)
            raise ValueError("simulated failure in a Phase 2 hook")
        r.frame_hooks.append(bad_hook)
        r.start()
        try:
            self.assertTrue(wait_until(lambda: len(calls) >= 5))
            self.assertTrue(r.status()["running"])
            self.assertTrue(r.status()["connected"])
        finally:
            r.stop()

    def test_stop_joins_the_thread_and_releases_the_camera(self):
        r = CameraRunner(cfg(self.clip), log)
        r.start()
        wait_until(lambda: r.latest_jpeg() is not None)
        r.stop()
        self.assertFalse(r.status()["running"])
        self.assertIsNone(r.stream.cap)


class Disconnection(unittest.TestCase):
    def test_bad_source_reports_disconnected_and_keeps_retrying(self):
        r = CameraRunner(cfg("D:/definitely/not/a/real/file.mp4"), log)
        started_ok = r.start()
        try:
            self.assertFalse(started_ok)
            st = r.status()
            self.assertFalse(st["connected"])
            self.assertIsNotNone(st["last_error"])
            self.assertTrue(st["running"])  # the thread keeps running and retrying, it doesn't exit
            self.assertTrue(wait_until(lambda: r.status()["connection_attempts"] >= 3, timeout=2.0))
        finally:
            r.stop()

    def test_recovers_once_the_source_becomes_reachable(self):
        tmp = tempfile.mkdtemp()
        clip = os.path.join(tmp, "clip.mp4")
        make_clip(clip, fps=10, duration_s=2.0)
        r = CameraRunner(cfg("D:/nope.mp4", reconnect_wait_seconds=0.05), log)
        r.start()
        try:
            self.assertTrue(wait_until(lambda: r.status()["connection_attempts"] >= 1))
            r.cfg.source = clip                     # "the camera comes back online"
            r.reconnect_now()
            self.assertTrue(wait_until(lambda: r.status()["connected"], timeout=3.0))
            self.assertTrue(wait_until(lambda: r.latest_jpeg() is not None))
        finally:
            r.stop()

    def test_manual_reconnect_on_a_healthy_camera_recovers(self):
        # reconnect_now() only signals the capture thread (see its docstring for why touching
        # self.stream from this thread is unsafe), so it doesn't disconnect synchronously - what's
        # testable from here is that a fresh connect() happens afterwards and the camera recovers.
        tmp = tempfile.mkdtemp()
        clip = os.path.join(tmp, "clip.mp4")
        make_clip(clip, fps=10, duration_s=2.0)
        r = CameraRunner(cfg(clip, reconnect_wait_seconds=0.05), log)
        r.start()
        try:
            self.assertTrue(wait_until(lambda: r.status()["connected"]))
            attempts_before = r.status()["connection_attempts"]
            r.reconnect_now()
            self.assertTrue(wait_until(lambda: r.status()["connection_attempts"] > attempts_before, timeout=3.0))
            self.assertTrue(wait_until(lambda: r.status()["connected"], timeout=3.0))
        finally:
            r.stop()


if __name__ == "__main__":
    unittest.main()
