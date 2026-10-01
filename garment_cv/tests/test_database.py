"""The Phase 6 database layer: schema creation, and the exact thread-safety bug this project has
already hit once for real (`EventStore`'s shared sqlite3 connection, see
`dashboard-redesign-lessons` in the main Reality Connector project) - proven here with a genuine
multi-threaded concurrent-write test, not just a single-threaded CRUD check.
"""
import tempfile
import threading
import unittest
from pathlib import Path

from garment_cv.backend.database.db import Database


class SchemaAndBasicIO(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmpdir.name) / "test.db")

    def tearDown(self):
        self.db.close()
        self.tmpdir.cleanup()

    def test_tables_exist_after_construction(self):
        rows = self.db.query("SELECT name FROM sqlite_master WHERE type='table'")
        names = {r["name"] for r in rows}
        self.assertIn("production_events", names)
        self.assertIn("workers", names)

    def test_insert_then_query_round_trips(self):
        cur = self.db.execute(
            "INSERT INTO production_events (camera_id, track_id, worker, stage, zone, start_ts, "
            "end_ts, duration_s, confidence, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("cam-1", 1, "track-1", "CUTTING", "Cutting Zone", 0.0, 5.0, 5.0, 0.9, 100.0),
        )
        self.assertEqual(cur.lastrowid, 1)
        rows = self.db.query("SELECT * FROM production_events WHERE id = ?", (1,))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stage"], "CUTTING")

    def test_reopening_the_same_file_keeps_existing_rows(self):
        self.db.execute(
            "INSERT INTO production_events (camera_id, track_id, worker, stage, zone, start_ts, "
            "end_ts, duration_s, confidence, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("cam-1", 1, "track-1", "IDLE", None, 0.0, 5.0, 5.0, 1.0, 100.0),
        )
        path = Path(self.tmpdir.name) / "test.db"
        self.db.close()
        db2 = Database(path)
        try:
            rows = db2.query("SELECT * FROM production_events")
            self.assertEqual(len(rows), 1)
        finally:
            db2.close()


class ConcurrentWrites(unittest.TestCase):
    """The real bug class: multiple threads hammering one shared connection without a lock
    corrupts sqlite3's internal statement state (IndexError / InterfaceError), not just slows
    things down - a single-threaded test would never catch it."""

    def test_many_threads_writing_at_once_lose_no_rows_and_raise_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "concurrent.db")
            errors = []

            def writer(n):
                try:
                    for i in range(20):
                        db.execute(
                            "INSERT INTO production_events (camera_id, track_id, worker, stage, "
                            "zone, start_ts, end_ts, duration_s, confidence, created_at) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (f"cam-{n}", i, f"track-{i}", "IDLE", None, 0.0, 1.0, 1.0, 1.0, 0.0),
                        )
                except Exception as e:  # pragma: no cover - only hit if the lock is missing/broken
                    errors.append(e)

            threads = [threading.Thread(target=writer, args=(n,)) for n in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(errors, [])
            rows = db.query("SELECT COUNT(*) AS n FROM production_events")
            self.assertEqual(rows[0]["n"], 200)
            db.close()


if __name__ == "__main__":
    unittest.main()
