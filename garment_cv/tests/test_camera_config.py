import os
import unittest

from garment_cv.backend.cameras.config import CameraConfig, redact_url


class SourceType(unittest.TestCase):
    def cfg(self, source: str, **kw) -> CameraConfig:
        base = dict(camera_id="c1", camera_name="Camera 1", source=source, camera_user="",
                   camera_password="", rtsp_transport="tcp", open_timeout_ms=8000,
                   read_retry_limit=5, reconnect_wait_seconds=3.0, reconnect_max_attempts=0,
                   startup_max_attempts=3, capture_buffer=None, ffmpeg_loglevel=None,
                   stream_max_width=960, jpeg_quality=80)
        base.update(kw)
        return CameraConfig(**base)

    def test_webcam_index(self):
        c = self.cfg("0")
        self.assertTrue(c.is_webcam_index)
        self.assertFalse(c.is_network_url or c.is_file_source)
        self.assertEqual(c.opencv_source(), 0)
        self.assertEqual(c.safe_source, "0")

    def test_network_url(self):
        c = self.cfg("rtsp://192.168.1.50:554/Streaming/Channels/101")
        self.assertTrue(c.is_network_url)
        self.assertFalse(c.is_webcam_index or c.is_file_source)
        self.assertEqual(c.opencv_source(), c.source)  # no credentials to merge in

    def test_file_source(self):
        c = self.cfg("D:/videos/sample.mp4")
        self.assertTrue(c.is_file_source)
        self.assertFalse(c.is_webcam_index or c.is_network_url)
        self.assertEqual(c.opencv_source(), c.source)

    def test_credentials_merged_in_and_redacted(self):
        c = self.cfg("rtsp://192.168.1.50/live", camera_user="admin", camera_password="s3cret!")
        merged = c.opencv_source()
        self.assertIn("admin:s3cret", merged.replace("%21", "!"))
        self.assertNotIn("s3cret", c.safe_source)
        self.assertIn("admin:***@", c.safe_source)

    def test_existing_credentials_in_url_are_kept_not_overwritten(self):
        c = self.cfg("rtsp://alice:pw1@192.168.1.50/live", camera_user="bob", camera_password="pw2")
        self.assertIn("alice:pw1@", c.opencv_source())

    def test_no_credentials_configured_leaves_url_untouched(self):
        c = self.cfg("rtsp://192.168.1.50/live")
        self.assertEqual(c.opencv_source(), c.source)

    def test_redact_url_standalone(self):
        self.assertEqual(redact_url("rtsp://admin:hunter2@10.0.0.9/stream"), "rtsp://admin:***@10.0.0.9/stream")
        self.assertEqual(redact_url("rtsp://10.0.0.9/stream"), "rtsp://10.0.0.9/stream")  # no creds, untouched


class FfmpegOptions(unittest.TestCase):
    def test_capture_options_string(self):
        c = SourceType().cfg("rtsp://x/y", rtsp_transport="udp", open_timeout_ms=5000)
        opts = c.ffmpeg_capture_options()
        self.assertIn("rtsp_transport;udp", opts)
        self.assertIn("timeout;5000000", opts)

    def test_loglevel_name_and_numeric(self):
        c = SourceType().cfg("rtsp://x/y", ffmpeg_loglevel="warning")
        self.assertEqual(c.opencv_ffmpeg_loglevel(), "24")
        c2 = SourceType().cfg("rtsp://x/y", ffmpeg_loglevel="16")
        self.assertEqual(c2.opencv_ffmpeg_loglevel(), "16")
        c3 = SourceType().cfg("rtsp://x/y", ffmpeg_loglevel=None)
        self.assertIsNone(c3.opencv_ffmpeg_loglevel())
        c4 = SourceType().cfg("rtsp://x/y", ffmpeg_loglevel="not-a-level")
        self.assertIsNone(c4.opencv_ffmpeg_loglevel())


class EnvLoading(unittest.TestCase):
    KEYS = ["GARMENT_CAMERA_SOURCE", "GARMENT_CAMERA_ID", "GARMENT_CAMERA_NAME", "GARMENT_RTSP_TRANSPORT",
            "GARMENT_READ_RETRY_LIMIT", "CAM2_GARMENT_CAMERA_SOURCE", "CAM2_GARMENT_CAMERA_ID"]

    def setUp(self):
        self._saved = {k: os.environ.pop(k, None) for k in self.KEYS}

    def tearDown(self):
        for k, v in self._saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    def test_defaults_with_nothing_set(self):
        c = CameraConfig.load()
        self.assertEqual(c.camera_id, "camera-1")
        self.assertEqual(c.rtsp_transport, "tcp")
        self.assertEqual(c.read_retry_limit, 5)

    def test_env_vars_are_read(self):
        os.environ["GARMENT_CAMERA_SOURCE"] = "0"
        os.environ["GARMENT_RTSP_TRANSPORT"] = "udp"
        os.environ["GARMENT_READ_RETRY_LIMIT"] = "9"
        c = CameraConfig.load()
        self.assertEqual((c.source, c.rtsp_transport, c.read_retry_limit), ("0", "udp", 9))

    def test_invalid_transport_falls_back_to_tcp(self):
        os.environ["GARMENT_RTSP_TRANSPORT"] = "carrier-pigeon"
        self.assertEqual(CameraConfig.load().rtsp_transport, "tcp")

    def test_prefix_isolates_a_second_camera(self):
        os.environ["GARMENT_CAMERA_SOURCE"] = "0"
        os.environ["GARMENT_CAMERA_ID"] = "camera-1"
        os.environ["CAM2_GARMENT_CAMERA_SOURCE"] = "1"
        os.environ["CAM2_GARMENT_CAMERA_ID"] = "camera-2"
        c1, c2 = CameraConfig.load(), CameraConfig.load(prefix="CAM2_")
        self.assertEqual((c1.source, c1.camera_id), ("0", "camera-1"))
        self.assertEqual((c2.source, c2.camera_id), ("1", "camera-2"))


if __name__ == "__main__":
    unittest.main()
