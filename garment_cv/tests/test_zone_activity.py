"""The `activity` extension to Zone/ZoneResolver (Phase 5's dependency on Phase 4)."""
import os
import tempfile
import unittest

from garment_cv.backend.zones.resolver import ZoneResolver
from garment_cv.backend.zones.store import ZoneError, ZoneStore

SQUARE = [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]


def new_store() -> ZoneStore:
    return ZoneStore(os.path.join(tempfile.mkdtemp(), "zones.json"))


class ZoneActivityField(unittest.TestCase):
    def test_add_with_an_activity(self):
        s = new_store()
        z = s.add_zone("cam-1", "Cutting Zone", SQUARE, activity="cutting")
        self.assertEqual(z.activity, "cutting")

    def test_add_without_an_activity_defaults_to_none(self):
        s = new_store()
        z = s.add_zone("cam-1", "General Area", SQUARE)
        self.assertIsNone(z.activity)

    def test_invalid_activity_rejected(self):
        s = new_store()
        with self.assertRaises(ZoneError):
            s.add_zone("cam-1", "X", SQUARE, activity="welding")

    def test_activity_is_case_insensitive(self):
        s = new_store()
        z = s.add_zone("cam-1", "X", SQUARE, activity="CUTTING")
        self.assertEqual(z.activity, "cutting")

    def test_update_sets_activity(self):
        s = new_store()
        z = s.add_zone("cam-1", "X", SQUARE)
        updated = s.update_zone(z.id, activity="marking")
        self.assertEqual(updated.activity, "marking")

    def test_update_with_no_activity_argument_leaves_it_unchanged(self):
        s = new_store()
        z = s.add_zone("cam-1", "X", SQUARE, activity="cutting")
        updated = s.update_zone(z.id, name="Renamed")
        self.assertEqual(updated.activity, "cutting")

    def test_clear_activity(self):
        s = new_store()
        z = s.add_zone("cam-1", "X", SQUARE, activity="cutting")
        updated = s.update_zone(z.id, clear_activity=True)
        self.assertIsNone(updated.activity)

    def test_survives_a_restart(self):
        path = os.path.join(tempfile.mkdtemp(), "zones.json")
        ZoneStore(path).add_zone("cam-1", "Cutting Zone", SQUARE, activity="cutting")
        s2 = ZoneStore(path)
        self.assertEqual(s2.list_zones()[0].activity, "cutting")

    def test_a_zone_saved_before_this_field_existed_loads_with_no_activity(self):
        # simulates an old zones.json from before Phase 5, written without the "activity" key
        path = os.path.join(tempfile.mkdtemp(), "zones.json")
        import json
        with open(path, "w") as f:
            json.dump({"zones": [{"id": 1, "camera_id": "cam-1", "name": "Old Zone", "points": SQUARE,
                                 "created_at": 0.0}], "next_id": 2}, f)
        s = ZoneStore(path)
        self.assertIsNone(s.get_zone(1).activity)


class ActivityForBox(unittest.TestCase):
    def test_returns_the_zones_activity_type(self):
        s = new_store()
        s.add_zone("cam-1", "Cutting Zone", SQUARE, activity="cutting")
        r = ZoneResolver(s)
        self.assertEqual(r.activity_for_box("cam-1", (50, 0, 150, 300), 1000, 1000), "cutting")
        self.assertEqual(r.zone_for_box("cam-1", (50, 0, 150, 300), 1000, 1000), "Cutting Zone")

    def test_none_when_the_zone_has_no_activity(self):
        s = new_store()
        s.add_zone("cam-1", "General Area", SQUARE)
        r = ZoneResolver(s)
        self.assertIsNone(r.activity_for_box("cam-1", (50, 0, 150, 300), 1000, 1000))
        self.assertEqual(r.zone_for_box("cam-1", (50, 0, 150, 300), 1000, 1000), "General Area")

    def test_none_when_in_no_zone_at_all(self):
        r = ZoneResolver(new_store())
        self.assertIsNone(r.activity_for_box("cam-1", (50, 0, 150, 300), 1000, 1000))


if __name__ == "__main__":
    unittest.main()
