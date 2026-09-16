"""Runs one stock check per configured shelf on this camera: count, record,
and raise a shelf alert (with a per-shelf cooldown) when stock is low.
"""
from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np

from phase4_events.store import EventStore, Shelf

from .config import Phase8Config
from .counter import BottleCounter
from .notify import notify_shelf_alert


class ShelfChecker:
    def __init__(self, cfg: Phase8Config, store: EventStore, counter: BottleCounter, logger) -> None:
        self.cfg = cfg
        self.store = store
        self.counter = counter
        self.log = logger
        self.snapshot_dir = Path(cfg.snapshot_dir)
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self._last_alert: dict[int, float] = {}   # shelf_id -> last alert time
        self.checks_run = 0
        self.alerts_raised = 0

    def run_once(self, frame) -> None:
        shelves = self.store.list_shelves(camera_id=self.cfg.camera_id, enabled_only=True)
        if not shelves:
            self.log.warning("No shelves configured for camera_id=%s - nothing to check. "
                             "Add one via POST /api/shelves.", self.cfg.camera_id)
            return

        h, w = frame.shape[:2]
        detections = self.counter.detect(frame)
        now = time.time()

        for shelf in shelves:
            count, hits = self.counter.count_in_region(detections, shelf.points, w, h)
            pct = count / max(shelf.capacity, 1)
            snap_rel = self._save_snapshot(frame, shelf, hits, count, pct)

            check = self.store.add_shelf_check(
                shelf_id=shelf.id, camera_id=shelf.camera_id, ts=now,
                detected_count=count, capacity=shelf.capacity, pct_full=round(pct, 4),
                snapshot=snap_rel,
            )
            self.checks_run += 1
            self.log.info("shelf check '%s': %d/%d (%.0f%%) -> %s",
                          shelf.name, count, shelf.capacity, pct * 100, snap_rel or "(no snapshot)")

            if pct <= shelf.low_stock_threshold:
                if now - self._last_alert.get(shelf.id, 0.0) >= shelf.cooldown_s:
                    self._last_alert[shelf.id] = now
                    self._raise_alert(check, shelf)

    def _raise_alert(self, check, shelf: Shelf) -> None:
        alert = self.store.create_shelf_alert(check, shelf)
        self.alerts_raised += 1
        self.log.warning(
            "SHELF ALERT #%d '%s' - %d/%d (%.0f%%) <= threshold %.0f%% - needs restock",
            alert.id, shelf.name, check.detected_count, shelf.capacity,
            check.pct_full * 100, shelf.low_stock_threshold * 100,
        )
        if shelf.notify_email:
            notify_shelf_alert(alert, self.log)

    def _save_snapshot(self, frame, shelf: Shelf, hits, count: int, pct: float) -> str | None:
        try:
            img = frame.copy()
            h, w = img.shape[:2]
            pts = (np.array(shelf.points) * [w, h]).astype(int)
            cv2.polylines(img, [pts], True, (60, 220, 60) if pct > shelf.low_stock_threshold else (60, 60, 220), 2)
            for d in hits:
                cv2.rectangle(img, (d.x1, d.y1), (d.x2, d.y2), (0, 200, 255), 1)
            label = f"{shelf.name}: {count}/{shelf.capacity} ({pct * 100:.0f}%)"
            cv2.rectangle(img, (0, h - 26), (w, h), (0, 0, 0), -1)
            cv2.putText(img, label, (8, h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                       (255, 255, 255), 1, cv2.LINE_AA)
            day_dir = self.snapshot_dir / time.strftime("%Y-%m-%d")
            day_dir.mkdir(parents=True, exist_ok=True)
            path = day_dir / f"shelf{shelf.id}-{int(time.time() * 1000)}.jpg"
            cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
            return str(path.relative_to(self.snapshot_dir)).replace("\\", "/")
        except Exception as e:  # noqa: BLE001 - snapshot is best-effort evidence
            self.log.warning("shelf snapshot failed: %s", e)
            return None
