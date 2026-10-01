"""Bottleneck baselines (spec section 18) - configured, never guessed."""
import os
import unittest

import garment_cv.backend.config as base_config
import garment_cv.backend.events.baselines as baselines_mod
from garment_cv.backend.events.baselines import load_baselines


class LoadBaselines(unittest.TestCase):
    def setUp(self):
        self._saved_loaded = base_config._ENV_LOADED
        base_config._ENV_LOADED = True  # skip reading the real .env file during this test
        self._saved_env = {k: os.environ.get(k) for k in baselines_mod._ENV_KEYS.values()}
        for k in baselines_mod._ENV_KEYS.values():
            os.environ.pop(k, None)

    def tearDown(self):
        base_config._ENV_LOADED = self._saved_loaded
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_unset_baseline_is_none(self):
        self.assertEqual(load_baselines(), {"MARKING": None, "CUTTING": None, "STITCHING": None,
                                            "PIECE_HANDLING": None})

    def test_set_baseline_is_parsed_as_float(self):
        os.environ["GARMENT_BASELINE_CUTTING_S"] = "300"
        self.assertEqual(load_baselines()["CUTTING"], 300.0)

    def test_malformed_baseline_falls_back_to_none_not_a_crash(self):
        os.environ["GARMENT_BASELINE_CUTTING_S"] = "not-a-number"
        self.assertIsNone(load_baselines()["CUTTING"])


if __name__ == "__main__":
    unittest.main()
