"""Runs vehicle detection+tracking on every frame (cheap - reuses Phase 2's
generic YOLO+BoT-SORT tracker with vehicle classes instead of person), and
attempts an OCR read on each not-yet-confirmed track on its own schedule
(expensive - throttled independently of frame rate). Commits a plate_number
via majority vote (phase13_vehicle/ocr.py) and logs one vehicle_event per
commit - once per real vehicle sighting, not once per frame.
"""
from __future__ import annotations

import time
from pathlib import Path

import cv2

from phase2_detect.config import Phase2Config
from phase2_detect.detector import PersonTracker
from phase4_events.store import EventStore

from .config import Phase13Config
from .ocr import PlateAccumulator, PlateReader


class VehicleTracker:
    def __init__(self, cfg: Phase13Config, store: EventStore, logger) -> None:
        self.cfg = cfg
        self.store = store
        self.log = logger
        self.snapshot_dir = Path(cfg.snapshot_dir)
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)

        vehicle_cfg = Phase2Config(
            camera=cfg.camera, model=cfg.model, device=cfg.device, conf=cfg.conf,
            iou=cfg.iou, imgsz=cfg.imgsz, classes=cfg.classes, tracker=cfg.tracker,
            process_every_n=1, half=cfg.half, log_level=cfg.log_level, log_dir=cfg.log_dir,
        )
        self.detector = PersonTracker(vehicle_cfg, logger)   # generic - just vehicle classes here
        self.reader = PlateReader(logger)
        self.acc = PlateAccumulator(min_readings=cfg.min_readings, stale_after_s=cfg.stale_after_s)

        self.events_logged = 0

    def warmup(self) -> None:
        self.detector.warmup()
        self.log.info("Warming up EasyOCR (first load is slow) ...")
        self.reader.warmup()

    def process_frame(self, frame) -> None:
        now = time.time()
        vehicles = [v for v in self.detector.process(frame) if v.track_id is not None]

        for v in vehicles:
            if not self.acc.should_attempt_ocr(v.track_id, now, self.cfg.ocr_interval_s):
                self.acc.touch(v.track_id, now)
                continue
            candidates = self.reader.read_candidates(
                frame, (v.x1, v.y1, v.x2, v.y2), self.cfg.min_ocr_conf)
            self.acc.add_reading(v.track_id, candidates, now)
            if self.acc.ready_to_commit(v.track_id):
                result = self.acc.commit(v.track_id)
                if result:
                    plate, votes, conf = result
                    snap_rel = self._save_snapshot(frame, v, plate, conf)
                    self._log_event(v.track_id, plate, votes, conf, snap_rel, now)

        # a track that's vanished (vehicle left frame) with unread-out
        # readings still gets logged, best-effort, just without a fresh crop
        for track_id in self.acc.prune_stale(now):
            result = self.acc.force_commit(track_id)
            if result:
                plate, votes, conf = result
                self._log_event(track_id, plate, votes, conf, None, now)

    def _log_event(self, track_id: int, plate: str, votes: int, conf: float,
                    snap_rel: str | None, ts: float) -> None:
        ev = self.store.add_vehicle_event(
            ts=ts, camera_id=self.cfg.camera_id, track_id=track_id, plate_number=plate,
            vote_count=votes, confidence=conf, snapshot=snap_rel,
        )
        self.events_logged += 1
        self.log.info("vehicle plate confirmed: %s (track %d, %d/%d votes, conf %.2f) -> event #%d",
                      plate, track_id, votes, self.cfg.min_readings, conf, ev.id)

    def _save_snapshot(self, frame, v, plate: str, conf: float) -> str | None:
        try:
            img = frame.copy()
            cv2.rectangle(img, (v.x1, v.y1), (v.x2, v.y2), (0, 200, 255), 2)
            label = f"{plate} ({conf:.2f})"
            cv2.putText(img, label, (v.x1, max(20, v.y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                       (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(img, label, (v.x1, max(20, v.y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                       (0, 255, 255), 1, cv2.LINE_AA)
            day_dir = self.snapshot_dir / time.strftime("%Y-%m-%d")
            day_dir.mkdir(parents=True, exist_ok=True)
            safe_plate = "".join(c for c in plate if c.isalnum()) or "plate"
            path = day_dir / f"vehicle-{safe_plate}-{int(time.time() * 1000)}.jpg"
            cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
            return str(path.relative_to(self.snapshot_dir)).replace("\\", "/")
        except Exception as e:  # noqa: BLE001 - snapshot is best-effort evidence
            self.log.warning("vehicle snapshot failed: %s", e)
            return None
