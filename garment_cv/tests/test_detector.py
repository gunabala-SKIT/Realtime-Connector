"""Runs the real model against a real photo (see coco_standin.py for why a synthetic image
wouldn't do: a plain colored rectangle is never going to be recognised as a person)."""
import logging
import os
import time
import unittest

import cv2

from garment_cv.backend.detection.coco_standin import COCO_TO_GARMENT, NOT_YET_DETECTABLE
from garment_cv.backend.detection.config import DetectionConfig
from garment_cv.backend.detection.detector import ObjectDetector

log = logging.getLogger("test")
log.addHandler(logging.NullHandler())

ASSET = os.path.join(os.path.dirname(__file__), "assets", "bus_small.jpg")


def cfg(**kw) -> DetectionConfig:
    base = dict(enabled=True, model="yolo11n.pt", device="", conf=0.35, iou=0.5, imgsz=320, half=False,
               detect_interval_s=0.0, stream_max_width=480, jpeg_quality=80)
    base.update(kw)
    return DetectionConfig(**base)


class RealModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image = cv2.imread(ASSET)
        assert cls.image is not None, f"test fixture missing: {ASSET}"
        cls.detector = ObjectDetector(cfg(), log)
        cls.detector.start()

    def test_model_loaded_with_a_real_coco_stand_in(self):
        self.assertTrue(self.detector.enabled, self.detector.disabled_reason)
        self.assertIn(0, self.detector._wanted_ids)   # person
        self.assertIn(76, self.detector._wanted_ids)  # scissors
        self.assertIn(60, self.detector._wanted_ids)  # dining table

    def test_finds_real_people_in_a_real_photo(self):
        dets = self.detector.detect(self.image)
        people = [d for d in dets if d.cls == "person"]
        self.assertGreater(len(people), 0, "expected at least one person in the test photo")
        for p in people:
            self.assertEqual(p.coco_name, "person")
            self.assertGreater(p.conf, 0.35)
            x1, y1, x2, y2 = p.box
            self.assertTrue(0 <= x1 < x2 <= self.image.shape[1])
            self.assertTrue(0 <= y1 < y2 <= self.image.shape[0])

    def test_never_reports_a_class_it_cannot_actually_detect(self):
        dets = self.detector.detect(self.image)
        seen = {d.cls for d in dets}
        self.assertTrue(seen.issubset(set(COCO_TO_GARMENT.values())))
        self.assertFalse(seen & set(NOT_YET_DETECTABLE))

    def test_annotate_draws_something_different_from_the_source(self):
        dets = self.detector.detect(self.image)
        self.assertTrue(dets)  # this photo should have detections to draw
        annotated = self.detector._annotate(self.image.copy(), dets)
        self.assertGreater(len(annotated), 500)
        self.assertEqual(annotated[:2], b"\xff\xd8")

    def test_observe_populates_status_and_a_jpeg(self):
        d = ObjectDetector(cfg(detect_interval_s=0.0), log)
        d.start()
        d.observe("cam-1", self.image, time.time())
        st = d.status()
        self.assertTrue(any(x["class"] == "person" for x in st["detections"]))
        self.assertIsNotNone(d.latest_jpeg())
        self.assertEqual(st["detected_classes"], sorted(COCO_TO_GARMENT.values()))
        self.assertEqual(sorted(st["not_yet_detectable"]), sorted(NOT_YET_DETECTABLE))

    def test_observe_is_throttled_by_detect_interval(self):
        d = ObjectDetector(cfg(detect_interval_s=10.0), log)
        d.start()
        d.observe("cam-1", self.image, 100.0)
        self.assertEqual(d.infer_count, 1)
        d.observe("cam-1", self.image, 100.5)  # well inside the 10s window
        self.assertEqual(d.infer_count, 1, "a second call inside detect_interval_s must not re-run inference")
        d.observe("cam-1", self.image, 111.0)  # past the window
        self.assertEqual(d.infer_count, 2)


class Disabled(unittest.TestCase):
    def test_disabled_by_config_never_loads_a_model(self):
        d = ObjectDetector(cfg(enabled=False), log)
        d.start()
        self.assertFalse(d.enabled)
        self.assertIsNone(d.model)
        self.assertIn("disabled", d.disabled_reason)

    def test_observe_on_a_disabled_detector_is_a_no_op(self):
        d = ObjectDetector(cfg(enabled=False), log)
        d.start()
        d.observe("cam-1", cv2.imread(ASSET), time.time())  # must not raise
        self.assertEqual(d.status()["detections"], [])
        self.assertIsNone(d.latest_jpeg())


if __name__ == "__main__":
    unittest.main()
