"""Zone enrichment is pure post-processing of already-computed detections (see `_with_zones`'s
docstring), so this is tested with hand-built `Detection`s - no model, no GPU, fast. End-to-end
proof that it also works through the real model + camera + API path is in test_api_zones.py."""
import logging
import os
import tempfile
import unittest

from garment_cv.backend.detection.config import DetectionConfig
from garment_cv.backend.detection.detector import Detection, ObjectDetector
from garment_cv.backend.zones.resolver import ZoneResolver
from garment_cv.backend.zones.store import ZoneStore

log = logging.getLogger("test")
log.addHandler(logging.NullHandler())

LEFT = [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]
RIGHT = [[0.5, 0.0], [1.0, 0.0], [1.0, 1.0], [0.5, 1.0]]


def det_cfg() -> DetectionConfig:
    return DetectionConfig(enabled=True, model="yolo11n.pt", device="", conf=0.35, iou=0.5, imgsz=320,
                           half=False, detect_interval_s=0.0, stream_max_width=480, jpeg_quality=80)


def resolver_with(zones: list[tuple[str, str, list]]) -> ZoneResolver:
    store = ZoneStore(os.path.join(tempfile.mkdtemp(), "zones.json"))
    for camera_id, name, points in zones:
        store.add_zone(camera_id, name, points)
    return ZoneResolver(store)


def person(box) -> Detection:
    return Detection(cls="person", coco_name="person", conf=0.9, box=box)


class WithZones(unittest.TestCase):
    def test_detections_get_the_right_zone_name(self):
        resolver = resolver_with([("cam-1", "Marking Zone", LEFT), ("cam-1", "Cutting Zone", RIGHT)])
        d = ObjectDetector(det_cfg(), log, zones=resolver)  # never calls .start() - no model needed
        left_box = (50, 0, 150, 300)     # feet land around x=0.1 of a 1000-wide frame
        right_box = (800, 0, 900, 300)   # feet land around x=0.85
        outside_camera_box = (50, 0, 150, 300)
        out = d._with_zones("cam-1", [person(left_box), person(right_box)], 1000, 1000)
        self.assertEqual([x.zone for x in out], ["Marking Zone", "Cutting Zone"])
        # a camera with no zones configured never gets a zone, even for the same coordinates
        out2 = d._with_zones("cam-2", [person(outside_camera_box)], 1000, 1000)
        self.assertEqual(out2[0].zone, None)

    def test_only_zone_is_rewritten_everything_else_untouched(self):
        resolver = resolver_with([("cam-1", "Marking Zone", LEFT)])
        d = ObjectDetector(det_cfg(), log, zones=resolver)
        original = Detection(cls="person", coco_name="person", conf=0.77, box=(50, 0, 150, 300), track_id=12)
        out = d._with_zones("cam-1", [original], 1000, 1000)[0]
        self.assertEqual((out.cls, out.coco_name, out.conf, out.box, out.track_id),
                        (original.cls, original.coco_name, original.conf, original.box, original.track_id))
        self.assertEqual(out.zone, "Marking Zone")

    def test_no_resolver_configured_leaves_detections_unchanged(self):
        d = ObjectDetector(det_cfg(), log, zones=None)
        dets = [person((50, 0, 150, 300))]
        self.assertEqual(d._with_zones("cam-1", dets, 1000, 1000), dets)

    def test_empty_detection_list(self):
        resolver = resolver_with([("cam-1", "Marking Zone", LEFT)])
        d = ObjectDetector(det_cfg(), log, zones=resolver)
        self.assertEqual(d._with_zones("cam-1", [], 1000, 1000), [])


class Drawing(unittest.TestCase):
    def test_annotate_draws_zone_outlines_without_crashing(self):
        import numpy as np
        resolver = resolver_with([("cam-1", "Marking Zone", LEFT)])
        d = ObjectDetector(det_cfg(), log, zones=resolver)
        frame = np.zeros((480, 640, 3), np.uint8)
        jpg_with_zone = d._annotate(frame.copy(), [], camera_id="cam-1")
        jpg_without = d._annotate(frame.copy(), [], camera_id=None)
        self.assertEqual(jpg_with_zone[:2], b"\xff\xd8")
        self.assertNotEqual(jpg_with_zone, jpg_without)  # the outline actually changed the pixels

    def test_annotate_with_a_camera_that_has_no_zones_matches_no_camera_at_all(self):
        import numpy as np
        resolver = resolver_with([("cam-1", "Marking Zone", LEFT)])
        d = ObjectDetector(det_cfg(), log, zones=resolver)
        frame = np.zeros((480, 640, 3), np.uint8)
        self.assertEqual(d._annotate(frame.copy(), [], camera_id="cam-2"),
                         d._annotate(frame.copy(), [], camera_id=None))


if __name__ == "__main__":
    unittest.main()
