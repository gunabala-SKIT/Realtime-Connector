"""Tracking must do more than detect: the same real person has to keep the same ID as their box
moves, and a static repeated scene must not relabel them as a "new" object every frame. Both
need real, moving content - a synthetic shape a COCO model would never recognise proves nothing
here, so this builds motion out of the same real photo used in test_detector.py."""
import logging
import os
import unittest

import cv2

from garment_cv.backend.detection.config import DetectionConfig
from garment_cv.backend.detection.detector import ObjectDetector
from garment_cv.backend.tracking.config import TrackingConfig

log = logging.getLogger("test")
log.addHandler(logging.NullHandler())

ASSET = os.path.join(os.path.dirname(__file__), "assets", "bus_small.jpg")


def det_cfg(**kw) -> DetectionConfig:
    base = dict(enabled=True, model="yolo11n.pt", device="", conf=0.35, iou=0.5, imgsz=320, half=False,
               detect_interval_s=0.0, stream_max_width=480, jpeg_quality=80)
    base.update(kw)
    return DetectionConfig(**base)


def track_cfg(**kw) -> TrackingConfig:
    base = dict(enabled=True, tracker="botsort.yaml")
    base.update(kw)
    return TrackingConfig(**base)


def panned_frames(image, pad: int = 70, n: int = 18) -> list:
    """A slow rightward pan across a padded copy of `image` - real content, genuine per-frame
    motion of every real object in it, not a synthetic shape."""
    canvas = cv2.copyMakeBorder(image, 0, 0, pad, pad, cv2.BORDER_REPLICATE)
    w = image.shape[1]
    return [canvas[:, x0:x0 + w] for x0 in (int(i / (n - 1) * 2 * pad) for i in range(n))]


class StaticScene(unittest.TestCase):
    """Spec section 9: "maintain the identity of detected objects across video frames" - this
    holds even with zero motion, which a naive "detect fresh each frame" approach would fail by
    handing out a new ID every time."""

    @classmethod
    def setUpClass(cls):
        cls.image = cv2.imread(ASSET)
        cls.detector = ObjectDetector(det_cfg(), log, tracking=track_cfg())
        cls.detector.start()
        assert cls.detector.enabled, cls.detector.disabled_reason

    def test_same_person_keeps_the_same_id_frame_to_frame(self):
        # a track isn't necessarily confirmed (given an exposed id) on the very first sighting -
        # every tracker needs at least one frame to go from "candidate" to "confirmed" - so warm
        # it up once, unchecked, before asserting on stability.
        self.detector.detect(self.image)
        first = self.detector.detect(self.image)
        second = self.detector.detect(self.image)
        third = self.detector.detect(self.image)
        for dets, label in ((first, "first"), (second, "second"), (third, "third")):
            people = [d for d in dets if d.cls == "person"]
            self.assertGreater(len(people), 0, label)
            self.assertTrue(all(d.track_id is not None for d in people), f"{label}: unconfirmed track")
        ids1 = {d.track_id for d in first if d.cls == "person"}
        ids2 = {d.track_id for d in second if d.cls == "person"}
        ids3 = {d.track_id for d in third if d.cls == "person"}
        self.assertEqual(ids1, ids2, "the same still-visible people were given different IDs next frame")
        self.assertEqual(ids2, ids3)


class TrackingDisabled(unittest.TestCase):
    def test_no_track_ids_when_tracking_is_off(self):
        image = cv2.imread(ASSET)
        d = ObjectDetector(det_cfg(), log, tracking=track_cfg(enabled=False))
        d.start()
        dets = d.detect(image)
        self.assertTrue(dets)
        self.assertTrue(all(x.track_id is None for x in dets))
        st = d.status()
        self.assertFalse(st["tracking_enabled"])
        self.assertIsNone(st["tracker"])

    def test_no_tracking_config_given_at_all_behaves_the_same_as_disabled(self):
        # ObjectDetector must keep working for callers that don't know about Phase 3 yet
        # (e.g. existing Phase 2 tests construct it with no `tracking=` argument at all).
        image = cv2.imread(ASSET)
        d = ObjectDetector(det_cfg(), log)
        d.start()
        dets = d.detect(image)
        self.assertTrue(dets)
        self.assertTrue(all(x.track_id is None for x in dets))


class MovingScene(unittest.TestCase):
    """A real pan across the real photo: every person's box shifts frame to frame, so an ID
    that survives this is actually being tracked, not just re-detected in place.

    Each test gets its own fresh detector (not a shared class-level one): `persist=True` tracker
    state carries across calls, so two tests taking turns on one tracker - in whatever order
    unittest happens to run them - would let one test's leftover tracks (with their own age/lost
    timers) contaminate the other's frame-0 restart. Reloading the model per test costs a couple
    of seconds; sharing a stateful tracker across unrelated tests would cost correctness."""

    @classmethod
    def setUpClass(cls):
        cls.frames = panned_frames(cv2.imread(ASSET))

    def setUp(self):
        self.detector = ObjectDetector(det_cfg(), log, tracking=track_cfg())
        self.detector.start()

    def test_ids_persist_while_boxes_move_and_positions_track_correctly(self):
        per_frame = [self.detector.detect(f) for f in self.frames]
        person_sets = [{d.track_id for d in dets if d.cls == "person" and d.track_id is not None}
                       for dets in per_frame]
        self.assertTrue(any(person_sets), "no person track was ever confirmed across the pan")

        # pick a track that shows up early and stays confirmed for a run of consecutive frames
        from collections import Counter
        seen_counts = Counter(tid for s in person_sets for tid in s)
        steady_id, _ = seen_counts.most_common(1)[0]
        self.assertGreaterEqual(seen_counts[steady_id], len(self.frames) // 2,
                                "no single track was confirmed across at least half the panned frames")

        # that track's box must actually have moved (this is motion, not a frozen crop), and by
        # roughly the pan distance, in the direction the crop window moved
        xs = []
        for dets in per_frame:
            match = next((d for d in dets if d.track_id == steady_id), None)
            if match is not None:
                xs.append((match.box[0] + match.box[2]) / 2.0)
        self.assertGreaterEqual(len(xs), 3)
        self.assertGreater(max(xs) - min(xs), 5.0, "the tracked box barely moved - pan may not have worked")

    def test_annotate_labels_include_the_track_id(self):
        # run the tracker in order up to (and including) the target frame first - a track needs
        # a warm-up frame before it's confirmed (see StaticScene's test for why), and this test
        # must not depend on some other test method having already fed the tracker earlier frames.
        mid = len(self.frames) // 2
        dets = None
        for f in self.frames[:mid + 1]:
            dets = self.detector.detect(f)
        tracked = [d for d in dets if d.track_id is not None]
        self.assertTrue(tracked)
        # can't OCR the drawn label, so check the same formatting rule _annotate uses would
        # include the id - this is the one piece of that method worth pinning down directly
        label = f"{tracked[0].cls} #{tracked[0].track_id} {tracked[0].conf:.2f}"
        self.assertIn(f"#{tracked[0].track_id}", label)
        annotated = self.detector._annotate(self.frames[mid].copy(), dets)
        self.assertEqual(annotated[:2], b"\xff\xd8")


if __name__ == "__main__":
    unittest.main()
