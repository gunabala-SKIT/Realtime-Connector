"""Manual worker assignment (spec section 13's explicit MVP allowance - no face recognition)."""
import tempfile
import unittest
from pathlib import Path

from garment_cv.backend.database.db import Database
from garment_cv.backend.workers.store import WorkerStore


class Assignment(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmpdir.name) / "workers.db")
        self.store = WorkerStore(self.db)

    def tearDown(self):
        self.db.close()
        self.tmpdir.cleanup()

    def test_unassigned_track_falls_back_to_track_dash_id(self):
        self.assertEqual(self.store.name_for(7), "track-7")

    def test_none_track_id_is_unknown(self):
        self.assertEqual(self.store.name_for(None), "unknown")

    def test_assign_then_name_for_returns_the_name(self):
        self.store.assign(3, "Alice")
        self.assertEqual(self.store.name_for(3), "Alice")

    def test_reassigning_the_same_track_overwrites_the_name(self):
        self.store.assign(3, "Alice")
        self.store.assign(3, "Priya")
        self.assertEqual(self.store.name_for(3), "Priya")
        self.assertEqual(len(self.store.list()), 1)

    def test_empty_name_rejected(self):
        with self.assertRaises(ValueError):
            self.store.assign(3, "   ")

    def test_unassign_reverts_to_fallback(self):
        self.store.assign(3, "Alice")
        self.store.unassign(3)
        self.assertEqual(self.store.name_for(3), "track-3")

    def test_list_returns_all_assignments(self):
        self.store.assign(1, "Alice")
        self.store.assign(2, "Bob")
        names = {w["name"] for w in self.store.list()}
        self.assertEqual(names, {"Alice", "Bob"})


if __name__ == "__main__":
    unittest.main()
