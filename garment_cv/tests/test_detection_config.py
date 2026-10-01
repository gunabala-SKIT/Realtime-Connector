import os
import unittest

from garment_cv.backend.detection.config import DetectionConfig


class EnvLoading(unittest.TestCase):
    KEYS = ["GARMENT_DETECT_ENABLED", "GARMENT_DETECT_MODEL", "GARMENT_DETECT_HALF",
            "GARMENT_DETECT_CONF", "GARMENT_DETECT_INTERVAL_S", "GARMENT_DETECT_DEVICE"]

    def setUp(self):
        self._saved = {k: os.environ.pop(k, None) for k in self.KEYS}

    def tearDown(self):
        for k, v in self._saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    def test_defaults(self):
        c = DetectionConfig.load()
        self.assertTrue(c.enabled)
        self.assertEqual(c.model, "yolo11n.pt")
        # GTX 16-series has no tensor cores - AMP/half-precision produces NaNs there, so the
        # default must stay False regardless of what other GPUs could tolerate.
        self.assertFalse(c.half)
        self.assertEqual(c.conf, 0.35)
        self.assertGreater(c.detect_interval_s, 0)

    def test_env_overrides(self):
        os.environ["GARMENT_DETECT_ENABLED"] = "false"
        os.environ["GARMENT_DETECT_MODEL"] = "yolo11s.pt"
        os.environ["GARMENT_DETECT_CONF"] = "0.5"
        os.environ["GARMENT_DETECT_HALF"] = "true"
        c = DetectionConfig.load()
        self.assertFalse(c.enabled)
        self.assertEqual(c.model, "yolo11s.pt")
        self.assertEqual(c.conf, 0.5)
        self.assertTrue(c.half)

    def test_bad_numeric_value_falls_back_to_default(self):
        os.environ["GARMENT_DETECT_CONF"] = "not-a-number"
        self.assertEqual(DetectionConfig.load().conf, 0.35)


if __name__ == "__main__":
    unittest.main()
