"""Runs the real model against a real photo (see coco_standin.py for why a synthetic image
wouldn't do: a plain colored rectangle is never going to be recognised as a person)."""
import logging
import os
import time
import unittest
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

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


class CustomModel(unittest.TestCase):
    """A model with ZERO class overlap with the COCO stand-in vocabulary (e.g. a fully
    custom-trained one - see `garment_cv/training/`) is a different situation from a misconfigured
    one, not an error: `start()` should fall back to using all of that model's own classes. Mocks
    `ultralytics.YOLO` instead of requiring a real trained weight on disk, so this doesn't depend
    on `garment_cv/training/train_synthetic.py` having been run locally."""

    def _fake_model(self, names: dict[int, str]):
        model = MagicMock()
        model.names = names
        model.track.return_value = None
        model.predict.return_value = None
        return model

    def test_falls_back_to_all_of_the_models_own_classes(self):
        fake = self._fake_model({0: "fabric", 1: "marking_tool", 2: "sewing_machine"})
        with patch("ultralytics.YOLO", return_value=fake):
            d = ObjectDetector(cfg(model="custom.pt"), log)
            d.start()
        self.assertTrue(d.enabled, d.disabled_reason)
        self.assertTrue(d._custom_model)
        self.assertEqual(sorted(d._wanted_ids), [0, 1, 2])

    def test_status_reports_the_custom_models_own_classes_not_the_coco_stand_ins(self):
        fake = self._fake_model({0: "fabric", 1: "marking_tool"})
        with patch("ultralytics.YOLO", return_value=fake):
            d = ObjectDetector(cfg(model="custom.pt"), log)
            d.start()
        st = d.status()
        self.assertTrue(st["custom_model"])
        self.assertEqual(st["detected_classes"], ["fabric", "marking_tool"])
        self.assertEqual(st["not_yet_detectable"], [])

    def test_a_garment_model_that_also_knows_person_and_scissors_uses_every_class(self):
        # the model trained on the real camera footage: shares `person`/`scissors` with COCO but
        # also has garment classes COCO lacks. Filtering it to the COCO overlap would have hidden
        # fabric/marking_tool/fabric_piece - found when pointing the dashboard at it.
        fake = self._fake_model({0: "person", 1: "fabric", 2: "scissors", 3: "marking_tool", 4: "fabric_piece"})
        with patch("ultralytics.YOLO", return_value=fake):
            d = ObjectDetector(cfg(model="live.pt"), log)
            d.start()
        self.assertTrue(d._custom_model)
        self.assertEqual(sorted(d._wanted_ids), [0, 1, 2, 3, 4])
        self.assertEqual(d.status()["detected_classes"],
                         ["fabric", "fabric_piece", "marking_tool", "person", "scissors"])

    def test_the_detector_tells_the_activity_engine_what_its_model_can_see(self):
        class Recorder:
            def __init__(self):
                self.vocab = None

            def configure(self, vocabulary):
                self.vocab = set(vocabulary)

            def update(self, *a, **k):
                return {}

        garment, coco_like = Recorder(), Recorder()
        with patch("ultralytics.YOLO", return_value=self._fake_model({0: "person", 1: "fabric", 2: "scissors"})):
            ObjectDetector(cfg(model="live.pt"), log, activities=garment).start()
        with patch("ultralytics.YOLO", return_value=self._fake_model({0: "person", 76: "scissors", 60: "dining table"})):
            ObjectDetector(cfg(model="coco.pt"), log, activities=coco_like).start()
        self.assertEqual(garment.vocab, {"person", "fabric", "scissors"})
        self.assertEqual(coco_like.vocab, {"person", "scissors", "table"})   # the product's names, post-mapping

    def test_a_model_that_still_overlaps_coco_is_not_treated_as_custom(self):
        # one real COCO class ("person") present alongside unrelated ones -> still COCO stand-in mode
        fake = self._fake_model({0: "person", 1: "something_else"})
        with patch("ultralytics.YOLO", return_value=fake):
            d = ObjectDetector(cfg(model="mixed.pt"), log)
            d.start()
        self.assertFalse(d._custom_model)
        self.assertEqual(d._wanted_ids, [0])  # only the COCO-recognised class is used, as before


class PerClassConfidence(unittest.TestCase):
    """Found on real footage: the trained model DID see the scissors while they were being used, but
    at 0.1-0.5 confidence, and the global 0.35 bar threw most of those away - so the engine saw the
    scissors flicker in and out and could never confirm cutting. A small, half-hidden tool needs a
    lower bar than a person does. Uses a mocked model so the exact confidences are controlled."""

    class _Tensor:
        def __init__(self, values):
            self._v = list(values)

        def tolist(self):
            return list(self._v)

        def __len__(self):
            return len(self._v)

    def _detector(self, **cfg_kw):
        fake = MagicMock()
        fake.names = {0: "person", 1: "scissors", 2: "fabric"}
        with patch("ultralytics.YOLO", return_value=fake):
            d = ObjectDetector(cfg(model="live.pt", **cfg_kw), log)
            d.start()
        T = self._Tensor
        # (class id, confidence): person .2 (below .35), person .5, scissors .2, scissors .05
        rows = [(0, 0.2), (0, 0.5), (1, 0.2), (1, 0.05)]
        boxes = MagicMock()
        boxes.xyxy, boxes.id = T([[0, 0, 10, 10]] * 4), None
        boxes.cls, boxes.conf = T([r[0] for r in rows]), T([r[1] for r in rows])
        boxes.__len__ = lambda self_: 4
        fake.predict.return_value = [MagicMock(boxes=boxes)]
        return d, fake

    def test_each_class_is_held_to_its_own_bar(self):
        d, _ = self._detector(conf=0.35, class_conf={"scissors": 0.10})
        got = sorted((x.cls, round(x.conf, 2)) for x in d.detect(np.zeros((64, 64, 3), np.uint8)))
        self.assertEqual(got, [("person", 0.5), ("scissors", 0.2)])   # person .2 and scissors .05 dropped

    def test_the_model_is_run_at_the_lowest_threshold_in_play(self):
        d, fake = self._detector(conf=0.35, class_conf={"scissors": 0.10})
        d.detect(np.zeros((64, 64, 3), np.uint8))
        self.assertEqual(fake.predict.call_args.kwargs["conf"], 0.10)

    def test_without_overrides_the_global_threshold_applies_to_everything(self):
        d, fake = self._detector(conf=0.35)
        got = [(x.cls) for x in d.detect(np.zeros((64, 64, 3), np.uint8))]
        self.assertEqual(got, ["person"])
        self.assertEqual(fake.predict.call_args.kwargs["conf"], 0.35)


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
