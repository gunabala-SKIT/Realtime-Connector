import os
import unittest

from garment_cv.backend.tracking.config import TrackingConfig


class EnvLoading(unittest.TestCase):
    KEYS = ["GARMENT_TRACK_ENABLED", "GARMENT_TRACKER"]

    def setUp(self):
        self._saved = {k: os.environ.pop(k, None) for k in self.KEYS}

    def tearDown(self):
        for k, v in self._saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    def test_defaults(self):
        c = TrackingConfig.load()
        self.assertTrue(c.enabled)
        self.assertEqual(c.tracker, "botsort.yaml")

    def test_env_overrides(self):
        os.environ["GARMENT_TRACK_ENABLED"] = "false"
        os.environ["GARMENT_TRACKER"] = "bytetrack.yaml"
        c = TrackingConfig.load()
        self.assertFalse(c.enabled)
        self.assertEqual(c.tracker, "bytetrack.yaml")

    def test_unknown_tracker_falls_back_to_botsort(self):
        os.environ["GARMENT_TRACKER"] = "some-made-up-tracker.yaml"
        self.assertEqual(TrackingConfig.load().tracker, "botsort.yaml")

    def test_tracker_name_is_case_insensitive(self):
        os.environ["GARMENT_TRACKER"] = "ByteTrack.yaml"
        self.assertEqual(TrackingConfig.load().tracker, "bytetrack.yaml")


if __name__ == "__main__":
    unittest.main()
