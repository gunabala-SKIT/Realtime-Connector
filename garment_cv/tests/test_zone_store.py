import os
import tempfile
import unittest

from garment_cv.backend.zones.store import ZoneError, ZoneStore

SQUARE = [[0.1, 0.1], [0.6, 0.1], [0.6, 0.6], [0.1, 0.6]]


def new_store() -> ZoneStore:
    return ZoneStore(os.path.join(tempfile.mkdtemp(), "zones.json"))


class Crud(unittest.TestCase):
    def test_add_list_get(self):
        s = new_store()
        z = s.add_zone("cam-1", "Cutting Zone", SQUARE)
        self.assertEqual(z.id, 1)
        self.assertEqual(z.camera_id, "cam-1")
        self.assertEqual(s.list_zones(), [z])
        self.assertEqual(s.list_zones("cam-1"), [z])
        self.assertEqual(s.list_zones("cam-2"), [])
        self.assertEqual(s.get_zone(1), z)
        self.assertIsNone(s.get_zone(999))

    def test_ids_increment_and_are_stable_across_deletes(self):
        s = new_store()
        a = s.add_zone("cam-1", "A", SQUARE)
        b = s.add_zone("cam-1", "B", SQUARE)
        s.delete_zone(a.id)
        c = s.add_zone("cam-1", "C", SQUARE)
        self.assertEqual([z.id for z in s.list_zones()], [b.id, c.id])
        self.assertEqual(c.id, 3)  # never reuses a deleted id

    def test_update_name_and_points(self):
        s = new_store()
        z = s.add_zone("cam-1", "Marking Zone", SQUARE)
        updated = s.update_zone(z.id, name="Marking Area")
        self.assertEqual(updated.name, "Marking Area")
        new_points = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]
        updated2 = s.update_zone(z.id, points=new_points)
        self.assertEqual(updated2.points, new_points)

    def test_delete(self):
        s = new_store()
        z = s.add_zone("cam-1", "X", SQUARE)
        s.delete_zone(z.id)
        self.assertEqual(s.list_zones(), [])
        with self.assertRaises(ZoneError):
            s.delete_zone(z.id)


class Validation(unittest.TestCase):
    def test_too_few_points(self):
        s = new_store()
        with self.assertRaises(ZoneError):
            s.add_zone("cam-1", "X", [[0, 0], [1, 1]])

    def test_points_out_of_0_1_range(self):
        s = new_store()
        with self.assertRaises(ZoneError):
            s.add_zone("cam-1", "X", [[0, 0], [2, 0], [1, 1]])

    def test_malformed_points(self):
        s = new_store()
        with self.assertRaises(ZoneError):
            s.add_zone("cam-1", "X", [[0, 0], "nope", [1, 1]])

    def test_empty_name_rejected(self):
        s = new_store()
        with self.assertRaises(ZoneError):
            s.add_zone("cam-1", "   ", SQUARE)

    def test_no_camera_id_rejected(self):
        s = new_store()
        with self.assertRaises(ZoneError):
            s.add_zone("", "X", SQUARE)

    def test_duplicate_name_on_same_camera_rejected_case_insensitively(self):
        s = new_store()
        s.add_zone("cam-1", "Cutting Zone", SQUARE)
        with self.assertRaises(ZoneError):
            s.add_zone("cam-1", "cutting zone", SQUARE)

    def test_same_name_allowed_on_a_different_camera(self):
        s = new_store()
        s.add_zone("cam-1", "Cutting Zone", SQUARE)
        z2 = s.add_zone("cam-2", "Cutting Zone", SQUARE)  # must not raise
        self.assertEqual(z2.camera_id, "cam-2")

    def test_rename_to_an_existing_name_on_the_same_camera_rejected(self):
        s = new_store()
        s.add_zone("cam-1", "A", SQUARE)
        b = s.add_zone("cam-1", "B", SQUARE)
        with self.assertRaises(ZoneError):
            s.update_zone(b.id, name="A")

    def test_update_or_delete_of_unknown_zone_raises(self):
        s = new_store()
        with self.assertRaises(ZoneError):
            s.update_zone(999, name="X")


class Persistence(unittest.TestCase):
    def test_zones_survive_a_restart(self):
        path = os.path.join(tempfile.mkdtemp(), "zones.json")
        s1 = ZoneStore(path)
        s1.add_zone("cam-1", "Cutting Zone", SQUARE)
        s1.add_zone("cam-1", "Marking Zone", [[0.0, 0.0], [0.3, 0.0], [0.3, 0.3]])

        s2 = ZoneStore(path)  # a fresh instance, as a restarted process would create
        zones = s2.list_zones("cam-1")
        self.assertEqual({z.name for z in zones}, {"Cutting Zone", "Marking Zone"})
        self.assertEqual(zones[0].points, SQUARE)

    def test_missing_file_starts_empty_not_an_error(self):
        s = ZoneStore(os.path.join(tempfile.mkdtemp(), "does-not-exist-yet.json"))
        self.assertEqual(s.list_zones(), [])

    def test_corrupt_file_starts_empty_not_a_crash(self):
        path = os.path.join(tempfile.mkdtemp(), "zones.json")
        with open(path, "w") as f:
            f.write("{not valid json")
        s = ZoneStore(path)  # must not raise
        self.assertEqual(s.list_zones(), [])


if __name__ == "__main__":
    unittest.main()
