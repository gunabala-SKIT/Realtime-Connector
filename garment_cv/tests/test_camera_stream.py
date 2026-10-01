"""Exercises the real OpenCV read path against a real (synthetic) video file - see
`make_test_clip.py` for why this uses an actual file instead of a mock."""
import logging
import os
import tempfile
import time
import unittest

from garment_cv.backend.cameras.config import CameraConfig
from garment_cv.backend.cameras.stream import CameraStream

from .make_test_clip import make_clip

log = logging.getLogger("test")
log.addHandler(logging.NullHandler())


def cfg(source: str, **kw) -> CameraConfig:
    base = dict(camera_id="c1", camera_name="Camera 1", source=source, camera_user="", camera_password="",
               rtsp_transport="tcp", open_timeout_ms=2000, read_retry_limit=3, reconnect_wait_seconds=0.05,
               reconnect_max_attempts=2, startup_max_attempts=2, capture_buffer=None, ffmpeg_loglevel=None,
               stream_max_width=960, jpeg_quality=80)
    base.update(kw)
    return CameraConfig(**base)


class FileSource(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.clip = os.path.join(cls.tmp, "clip.mp4")
        cls.meta = make_clip(cls.clip, fps=10, duration_s=2.0)  # 20 frames

    def test_connect_and_read_real_frames(self):
        s = CameraStream(cfg(self.clip), log)
        self.assertTrue(s.open())
        self.assertTrue(s.is_connected())
        ok, frame = s.read_frame()
        self.assertTrue(ok)
        self.assertEqual(frame.shape[:2], (self.meta["height"], self.meta["width"]))
        st = s.get_status()
        self.assertTrue(st["connected"])
        self.assertEqual(st["resolution"], f"{self.meta['width']}x{self.meta['height']}")
        self.assertIsNone(st["last_error"])
        s.release()

    def test_file_is_paced_to_its_own_frame_rate(self):
        # 20 frames at 10 fps should take roughly 2s to read once through, not near-zero -
        # this is the same bug class documented in phase1_camera and manufacturing_intel:
        # an unpaced file source is decoded as fast as the disk allows.
        s = CameraStream(cfg(self.clip), log)
        self.assertTrue(s.open())
        t0 = time.monotonic()
        n = self.meta["frames"] - 1  # open() already consumed the first frame during warm-up
        for _ in range(n):
            ok, _ = s.read_frame()
            self.assertTrue(ok)
        elapsed = time.monotonic() - t0
        self.assertGreater(elapsed, self.meta["duration_s"] * 0.6, "file played back faster than real time")
        s.release()

    def test_end_of_file_loops_instead_of_failing(self):
        s = CameraStream(cfg(self.clip), log)
        self.assertTrue(s.open())
        ok = True
        for _ in range(self.meta["frames"] * 2 + 3):  # run past EOF more than once
            ok, frame = s.read_frame()
            if not ok:
                break
        self.assertTrue(ok, "a looping file source should never report a failed read")
        s.release()

    def test_fps_metric_is_plausible_after_a_few_reads(self):
        s = CameraStream(cfg(self.clip), log)
        self.assertTrue(s.open())
        for _ in range(8):
            s.read_frame()
        fps = s.metrics.actual_fps
        self.assertTrue(6.0 <= fps <= 14.0, f"actual_fps={fps} far from the clip's 10 fps")
        s.release()


class BadSource(unittest.TestCase):
    def test_missing_file_fails_to_connect_without_raising(self):
        s = CameraStream(cfg("D:/definitely/not/a/real/file.mp4"), log)
        self.assertFalse(s.connect())
        self.assertIsNotNone(s.metrics.last_error)
        self.assertFalse(s.is_connected())

    def test_read_before_connect_reports_failure_not_an_exception(self):
        s = CameraStream(cfg("D:/nope.mp4"), log)
        ok, frame = s.read_frame()
        self.assertEqual((ok, frame), (False, None))

    def test_open_gives_up_after_startup_max_attempts(self):
        s = CameraStream(cfg("D:/nope.mp4", startup_max_attempts=2, reconnect_wait_seconds=0.02), log)
        t0 = time.monotonic()
        self.assertFalse(s.open())
        self.assertLess(time.monotonic() - t0, 5.0)  # bounded, not an infinite/very slow retry loop
        self.assertEqual(s.metrics.connection_attempts, 2)

    def test_reconnect_respects_max_attempts_then_gives_up(self):
        s = CameraStream(cfg("D:/nope.mp4", reconnect_max_attempts=2, reconnect_wait_seconds=0.02), log)
        self.assertFalse(s.reconnect())
        self.assertEqual(s.metrics.reconnections, 1)


class RecoveryAfterOutage(unittest.TestCase):
    def test_stream_recovers_once_a_valid_source_reconnects(self):
        """Simulates a camera coming back online: the same CameraStream, pointed first at a bad
        source and then reconnected against a real file, ends up connected."""
        tmp = tempfile.mkdtemp()
        clip = os.path.join(tmp, "clip.mp4")
        make_clip(clip, fps=10, duration_s=1.0)
        s = CameraStream(cfg("D:/nope.mp4", startup_max_attempts=1), log)
        self.assertFalse(s.open())
        s.cfg.source = clip  # the "camera" is back - point the same stream at the real source
        self.assertTrue(s.connect())
        self.assertTrue(s.is_connected())
        s.release()


if __name__ == "__main__":
    unittest.main()
