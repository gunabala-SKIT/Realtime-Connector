import os
import tempfile
import unittest

from garment_cv.backend.zones.resolver import ZoneResolver
from garment_cv.backend.zones.store import ZoneStore

LEFT = [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]
RIGHT = [[0.5, 0.0], [1.0, 0.0], [1.0, 1.0], [0.5, 1.0]]


def new_store() -> ZoneStore:
    return ZoneStore(os.path.join(tempfile.mkdtemp(), "zones.json"))


class Resolving(unittest.TestCase):
    def test_box_in_the_left_zone(self):
        s = new_store()
        s.add_zone("cam-1", "Marking Zone", LEFT)
        s.add_zone("cam-1", "Cutting Zone", RIGHT)
        r = ZoneResolver(s)
        # a 100x300 box near the left edge of a 1000x1000 frame - feet land around x=0.1
        self.assertEqual(r.zone_for_box("cam-1", (50, 0, 150, 300), 1000, 1000), "Marking Zone")

    def test_box_in_the_right_zone(self):
        s = new_store()
        s.add_zone("cam-1", "Marking Zone", LEFT)
        s.add_zone("cam-1", "Cutting Zone", RIGHT)
        r = ZoneResolver(s)
        self.assertEqual(r.zone_for_box("cam-1", (800, 0, 900, 300), 1000, 1000), "Cutting Zone")

    def test_box_in_neither_zone_is_none(self):
        s = new_store()
        s.add_zone("cam-1", "Marking Zone", LEFT)
        r = ZoneResolver(s)
        self.assertIsNone(r.zone_for_box("cam-1", (800, 0, 900, 300), 1000, 1000))

    def test_no_zones_configured_at_all(self):
        r = ZoneResolver(new_store())
        self.assertIsNone(r.zone_for_box("cam-1", (10, 10, 50, 50), 1000, 1000))

    def test_zones_are_per_camera(self):
        s = new_store()
        s.add_zone("cam-1", "Marking Zone", LEFT)
        r = ZoneResolver(s)
        # the exact same box, on a camera with no zones configured, resolves to nothing
        self.assertIsNone(r.zone_for_box("cam-2", (50, 0, 150, 300), 1000, 1000))

    def test_overlapping_zones_the_earliest_added_wins(self):
        s = new_store()
        s.add_zone("cam-1", "Whole Room", [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])
        s.add_zone("cam-1", "Cutting Corner", [[0.6, 0.6], [1.0, 0.6], [1.0, 1.0], [0.6, 1.0]])
        r = ZoneResolver(s)
        # a point inside both zones resolves to whichever was configured first
        self.assertEqual(r.zone_for_box("cam-1", (800, 800, 850, 850), 1000, 1000), "Whole Room")


if __name__ == "__main__":
    unittest.main()
