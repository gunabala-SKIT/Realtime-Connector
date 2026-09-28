"""End-to-end camera test: a synthetic conveyor video with known ground truth goes through the
real adapter (background learning, tracking, zones, debouncing) and the real engine; what was
recorded must match what the video actually shows."""
import os
import tempfile
import unittest

import cv2

from manufacturing_intel.engine import ProductionEngine
from manufacturing_intel.store import Store
from manufacturing_intel.synth_video import FPS, PartSpec, make_video, zone_polys_normalised
from manufacturing_intel.vision import BackgroundDetector, CameraRunner, CentroidTracker, point_in_polygon

NOW = 1_800_000_000.0


class Rig:
    """Line: Raw -> S1 -> S2 -> S3 -> Done, all three process stations watched by one camera."""

    def __init__(self, video: str, meta: dict, detector: str = "motion", decode_qr: bool = False, params=None):
        self.store = Store(":memory:")
        self.store.ensure_factory("Test")
        line = self.store.add_line("Cam Line")["id"]
        self.cam = self.store.add_camera("Belt cam", video, line, detector, params or {}, decode_qr)
        prod = self.store.add_product("PART", "Part", 5)["id"]
        raw = self.store.add_station(line, "Raw", "source")["id"]
        zones = zone_polys_normalised()
        st = [self.store.add_station(line, f"S{i}", "process", camera_id=self.cam["id"], zone=zones[f"S{i}"])["id"] for i in (1, 2, 3)]
        done = self.store.add_station(line, "Done", "sink")["id"]
        self.store.save_route(line, prod, [raw, *st, done])
        self.t_start = NOW - meta["duration_s"] - 5
        self.cur = [self.t_start]
        self.engine = ProductionEngine(self.store, clock=lambda: NOW)
        self.runner = CameraRunner(self.store.get_camera(self.cam["id"]), self.store, self.engine, clock=lambda: self.cur[0])
        self.video, self.meta = video, meta

    def play(self) -> None:
        cap = cv2.VideoCapture(self.video)
        i = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            self.cur[0] = self.t_start + i / FPS
            self.runner.step(frame, self.cur[0], annotate=(i % 50 == 0))
            i += 1
        cap.release()
        self.runner.flush(self.cur[0], everything=True)

    def visits(self):
        rows = self.store.q("SELECT u.code, u.created_at, u.status, s.name station, v.start_ts, v.end_ts, v.processing_s, v.waiting_s "
                            "FROM visits v JOIN units u ON u.id=v.unit_id JOIN stations s ON s.id=v.station_id "
                            "WHERE s.kind='process' ORDER BY u.created_at, u.id, v.step_seq")
        out: dict[str, dict] = {}
        for r in rows:
            out.setdefault(r["code"], {})[r["station"]] = r
        return list(out.values())


class Standard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.video = os.path.join(cls.tmp, "belt.mp4")
        cls.meta = make_video(cls.video)
        cls.rig = Rig(cls.video, cls.meta)
        cls.rig.play()

    def test_every_part_is_found_once_per_station(self):
        v = self.rig.visits()
        self.assertEqual(len(v), 5)                                    # 5 parts -> 5 units, no phantoms, no misses
        for unit in v:
            self.assertEqual(set(unit), {"S1", "S2", "S3"})
        self.assertEqual(self.rig.store.scalar("SELECT COUNT(*) FROM units WHERE status='finished'"), 5)
        self.assertEqual(self.rig.runner.refused, 0)

    def test_processing_times_match_the_video(self):
        v = self.rig.visits()
        for i, part in enumerate(self.meta["parts"]):
            for zone in ("S1", "S2", "S3"):
                (enter, leave), = part["zones"][zone]
                truth = leave - enter
                got = v[i][zone]["processing_s"]
                self.assertAlmostEqual(got, truth, delta=0.6, msg=f"part {i} {zone}: recorded {got:.2f}s, video {truth:.2f}s")

    def test_units_are_matched_in_order_across_stations(self):
        # FIFO: the k-th part into S1 is the k-th unit; its S3 exit must be its own, not a neighbour's
        v = self.rig.visits()
        for i, part in enumerate(self.meta["parts"]):
            (e1, _), = part["zones"]["S1"]
            (_, l3), = part["zones"]["S3"]
            u = v[i]
            self.assertAlmostEqual(u["S1"]["start_ts"] - self.rig.t_start, e1, delta=0.5)
            self.assertAlmostEqual(u["S3"]["end_ts"] - self.rig.t_start, l3, delta=0.5)

    def test_waiting_time_is_the_travel_between_zones(self):
        v = self.rig.visits()
        for i, part in enumerate(self.meta["parts"]):
            (_, l1), = part["zones"]["S1"]
            (e2, _), = part["zones"]["S2"]
            self.assertAlmostEqual(v[i]["S2"]["waiting_s"], e2 - l1, delta=0.6)

    def test_events_are_logged_as_camera_events(self):
        n = self.rig.store.scalar("SELECT COUNT(*) FROM events WHERE source='camera' AND result='applied'")
        self.assertEqual(n, 5 * 3 * 2)                                 # enter + exit at three stations, five parts
        self.assertEqual(self.rig.store.scalar("SELECT COUNT(*) FROM events WHERE result!='applied'"), 0)


class HardCases(unittest.TestCase):
    def run_rig(self, parts, **kw):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "v.mp4")
        meta = make_video(path, parts)
        rig = Rig(path, meta, **kw)
        rig.play()
        return rig, meta

    def test_a_part_parked_for_ninety_seconds_is_not_absorbed_into_the_background(self):
        rig, meta = self.run_rig([PartSpec(5.0, (90.0, 3.0, 3.0))])
        u = rig.visits()[0]
        (enter, leave), = meta["parts"][0]["zones"]["S1"]        # 90 s parked + the ride through the zone
        self.assertGreater(leave - enter, 90.0)
        self.assertAlmostEqual(u["S1"]["processing_s"], leave - enter, delta=0.6)
        self.assertEqual(rig.store.scalar("SELECT COUNT(*) FROM units"), 1)      # never 'left' and came back as a new part

    def test_a_part_jittering_across_a_zone_edge_is_one_visit(self):
        rig, meta = self.run_rig([PartSpec(5.0, (20.0, 3.0, 3.0), edge_jitter=True)])
        n = rig.store.scalar("SELECT COUNT(*) FROM events WHERE station_id=(SELECT id FROM stations WHERE name='S1') AND result='applied'")
        self.assertLessEqual(n, 2)                                               # one enter (+ one exit), not a stream of them
        self.assertEqual(rig.store.scalar("SELECT COUNT(*) FROM units"), 1)

    def test_qr_code_becomes_the_unit_code(self):
        parts = [PartSpec(5.0, (4.0, 4.0, 4.0), size=96, qr="PART-000042"), PartSpec(16.0, (4.0, 4.0, 4.0), size=96, qr="PART-000043")]
        if cv2.QRCodeEncoder is None:                                            # pragma: no cover
            self.skipTest("this OpenCV build has no QR encoder")
        rig, meta = self.run_rig(parts, decode_qr=True)
        codes = [r["code"] for r in rig.store.q("SELECT code FROM units ORDER BY id")]
        self.assertEqual(codes, ["PART-000042", "PART-000043"])
        self.assertEqual(rig.store.scalar("SELECT COUNT(*) FROM units WHERE status='finished'"), 2)


class Pieces(unittest.TestCase):
    def test_point_in_polygon(self):
        sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
        self.assertTrue(point_in_polygon(5, 5, sq))
        self.assertFalse(point_in_polygon(11, 5, sq))
        tri = [(0, 0), (10, 0), (0, 10)]
        self.assertTrue(point_in_polygon(2, 2, tri))
        self.assertFalse(point_in_polygon(8, 8, tri))

    def test_tracker_keeps_identity_through_a_short_occlusion(self):
        t = CentroidTracker(lost_s=2.0)
        a, _ = t.update([(10, 10, 50, 50)], 0.0)
        t.update([], 0.5)                                # vanishes for a moment
        b, dropped = t.update([(14, 10, 54, 50)], 1.0)   # reappears nearby
        self.assertEqual(a[0].id, b[0].id)
        self.assertEqual(dropped, [])
        _, dropped = t.update([], 5.0)
        self.assertEqual([d[0] for d in dropped], [a[0].id])

    def test_background_detector_learns_then_sees(self):
        import numpy as np
        d = BackgroundDetector(warmup_frames=5, min_area=0.001)
        empty = np.full((120, 160, 3), 100, np.uint8)
        for _ in range(6):
            self.assertEqual(d.detect(empty), [])
        scene = empty.copy()
        cv2.rectangle(scene, (40, 40), (80, 80), (250, 250, 250), -1)
        boxes = d.detect(scene)
        self.assertEqual(len(boxes), 1)
        x1, y1, x2, y2 = boxes[0]
        self.assertTrue(abs(x1 - 40) <= 3 and abs(x2 - 81) <= 3)


if __name__ == "__main__":
    unittest.main()
