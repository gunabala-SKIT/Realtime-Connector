"""Object counting for shelf monitoring - detection only, no tracking.

Bottles don't move between checks, so unlike Phase 2 (person detection +
BoT-SORT), this runs a plain `model.predict()` every `check_interval_s`
instead of tracking every frame. Counting is restricted to each shelf's
polygon using the same point-in-polygon test Phase 7 uses for zones - a
detection "counts" if its box centre falls inside the shelf region, so two
overlapping shelves in one camera view don't double-count each other's stock.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from phase7_zones.geometry import point_in_polygon

from .config import Phase8Config


@dataclass
class Detection:
    x1: int
    y1: int
    x2: int
    y2: int
    conf: float

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0)


class BottleCounter:
    def __init__(self, cfg: Phase8Config, logger) -> None:
        self.cfg = cfg
        self.log = logger

        from ultralytics import YOLO

        self.log.info("Loading model %s (classes=%s) ...", cfg.model, cfg.classes)
        self.model = YOLO(cfg.model)
        self.device = self._resolve_device(cfg.device)
        self.log.info("Shelf counter ready. device=%s imgsz=%d conf=%.2f",
                      self.device, cfg.imgsz, cfg.conf)

    def _resolve_device(self, pref: str) -> str:
        if pref:
            return pref
        try:
            import torch
            if torch.cuda.is_available():
                return "0"
        except Exception:  # noqa: BLE001
            pass
        return "cpu"

    def warmup(self) -> None:
        blank = np.zeros((self.cfg.imgsz, self.cfg.imgsz, 3), dtype=np.uint8)
        self.model.predict(blank, classes=self.cfg.classes, imgsz=self.cfg.imgsz,
                           device=self.device, verbose=False)

    def detect(self, frame: np.ndarray) -> list[Detection]:
        """All detections of the configured class(es) in the whole frame."""
        r = self.model.predict(
            frame, classes=self.cfg.classes, conf=self.cfg.conf,
            imgsz=self.cfg.imgsz, device=self.device, verbose=False,
        )[0]
        out: list[Detection] = []
        b = getattr(r, "boxes", None)
        if b is not None and b.xyxy is not None and len(b) > 0:
            for (x1, y1, x2, y2), c in zip(b.xyxy.cpu().numpy(), b.conf.cpu().numpy()):
                out.append(Detection(int(x1), int(y1), int(x2), int(y2), float(c)))
        return out

    @staticmethod
    def count_in_region(detections: list[Detection], polygon: list,
                        frame_w: int, frame_h: int) -> tuple[int, list[Detection]]:
        """How many detections fall inside `polygon` (normalised 0..1 points)."""
        hits = []
        for d in detections:
            cx, cy = d.center
            pt = (cx / max(frame_w, 1), cy / max(frame_h, 1))
            if point_in_polygon(pt, polygon):
                hits.append(d)
        return len(hits), hits
