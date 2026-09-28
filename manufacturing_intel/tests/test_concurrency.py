"""Two writers on one database file (a camera box and a scanner gateway, or the server and the demo
feeder) must not fail with 'database is locked'. Separate Store objects = separate connections, the
same situation as separate processes."""
import os
import tempfile
import threading
import time
import unittest

from manufacturing_intel.analytics import Analytics
from manufacturing_intel.blueprint import RAW, build_metal_factory
from manufacturing_intel.engine import ProductionEngine
from manufacturing_intel.store import Store


class TwoWriters(unittest.TestCase):
    def test_concurrent_writers_do_not_hit_database_is_locked(self):
        path = os.path.join(tempfile.mkdtemp(), "shared.db")
        a = Store(path)
        ids = build_metal_factory(a, products=("GEAR",))
        a.add_batch(ids["products"]["GEAR"], ids["lines"]["Line A"], created_at=time.time() - 3600)
        b = Store(path)
        st = ids["stations"]["Line A"]
        errors: list[str] = []
        n = 150

        def writer(store: Store, prefix: str):
            eng = ProductionEngine(store)
            t0 = time.time() - 1800
            try:
                for i in range(n):
                    code = f"{prefix}-{i:05d}"
                    for typ, station, dt in (("issue", RAW, 0), ("enter", "Cutting", 1), ("exit", "Cutting", 2)):
                        r = eng.record(typ, st[station], ts=t0 + i * 3 + dt, unit_code=code, product="GEAR", source="test")
                        if r["result"] != "applied":
                            errors.append(f"{code} {typ}: {r}")
            except Exception as e:  # noqa: BLE001
                errors.append(f"{prefix}: {type(e).__name__}: {e}")

        def reader(store: Store, stop: threading.Event):
            an = Analytics(store)
            try:
                while not stop.is_set():
                    an.kpis(an.window("last_24h"), None)
            except Exception as e:  # noqa: BLE001
                errors.append(f"reader: {type(e).__name__}: {e}")

        stop = threading.Event()
        threads = [threading.Thread(target=writer, args=(a, "AAA")), threading.Thread(target=writer, args=(b, "BBB")),
                   threading.Thread(target=reader, args=(b, stop))]
        for t in threads:
            t.start()
        for t in threads[:2]:
            t.join(timeout=120)
        stop.set()
        threads[2].join(timeout=10)
        self.assertEqual(errors, [])
        self.assertEqual(a.scalar("SELECT COUNT(*) FROM units"), 2 * n)
        self.assertEqual(a.scalar("SELECT COUNT(*) FROM events WHERE result='applied'"), 2 * n * 3)


if __name__ == "__main__":
    unittest.main()
