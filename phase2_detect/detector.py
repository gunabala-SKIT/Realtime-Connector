"""Person detection + tracking.

Wraps Ultralytics ``YOLO.track()`` (YOLO11 + BoT-SORT / ByteTrack). The tracker
keeps its own state across calls via ``persist=True``; this class just adapts the
result into plain :class:`Track` records and decimates frames per
``PROCESS_EVERY_N``.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

from .config import Phase2Config


@dataclass
class Track:
    track_id: Optional[int]        # None until the tracker locks on
    cls: int                      # COCO class id (0 = person)
    conf: float
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def cx(self) -> int:
        return (self.x1 + self.x2) // 2

    @property
    def cy(self) -> int:
        return (self.y1 + self.y2) // 2


class PersonTracker:
    def __init__(self, cfg: Phase2Config, logger) -> None:
        self.cfg = cfg
        self.log = logger

        from ultralytics import YOLO  # imported here so config-only use stays light

        self.log.info("Loading model %s ...", cfg.model)
        self.model = YOLO(cfg.model)
        self.device = self._resolve_device(cfg.device)
        self.log.info(
            "Model ready. device=%s tracker=%s imgsz=%d conf=%.2f classes=%s",
            self.device, cfg.tracker, cfg.imgsz, cfg.conf, cfg.classes,
        )

        self._frame_i = 0
        self._last: list[Track] = []
        self._infer_ms = 0.0
        self.last_raw: list[Track] = []

    def _resolve_device(self, pref: str) -> str:
        if pref:
            return pref
        try:
            import torch
            if torch.cuda.is_available():
                return "0"
        except Exception:  # noqa: BLE001 - torch missing / broken -> CPU
            pass
        return "cpu"

    @property
    def last_infer_ms(self) -> float:
        return self._infer_ms

    def warmup(self) -> None:
        """Run a couple of dummy passes so the first real frame isn't a 10 s stall
        (cuDNN autotuning + CUDA context + graph build happen on the first call)."""
        blank = np.zeros((self.cfg.imgsz, self.cfg.imgsz, 3), dtype=np.uint8)
        for _ in range(2):
            self.model.track(blank, persist=False, tracker=self.cfg.tracker,
                             imgsz=self.cfg.imgsz, device=self.device, verbose=False)

    def raw_detect(self, frame: np.ndarray) -> list[Track]:
        """Plain detection (no tracker), for the --raw debug overlay: shows what
        the model sees before BoT-SORT filters/associates it."""
        r = self.model.predict(
            frame, classes=self.cfg.classes, conf=self.cfg.conf, iou=self.cfg.iou,
            imgsz=self.cfg.imgsz, device=self.device, half=self.cfg.half, verbose=False,
        )[0]
        out: list[Track] = []
        b = getattr(r, "boxes", None)
        if b is not None and b.xyxy is not None and len(b) > 0:
            for (x1, y1, x2, y2), c, cl in zip(
                b.xyxy.cpu().numpy(), b.conf.cpu().numpy(), b.cls.cpu().numpy().astype(int)
            ):
                out.append(Track(None, int(cl), float(c), int(x1), int(y1), int(x2), int(y2)))
        self.last_raw = out
        return out

    def process(self, frame: np.ndarray) -> list[Track]:
        """Run detection+tracking on this frame (or reuse the last result if this
        frame is being skipped per PROCESS_EVERY_N)."""
        run_now = (self._frame_i % self.cfg.process_every_n) == 0
        self._frame_i += 1
        if not run_now:
            return self._last

        t0 = time.perf_counter()
        results = self.model.track(
            frame,
            persist=True,
            tracker=self.cfg.tracker,
            classes=self.cfg.classes,
            conf=self.cfg.conf,
            iou=self.cfg.iou,
            imgsz=self.cfg.imgsz,
            device=self.device,
            half=self.cfg.half,
            verbose=False,
        )
        self._infer_ms = (time.perf_counter() - t0) * 1000.0

        tracks: list[Track] = []
        r = results[0]
        boxes = getattr(r, "boxes", None)
        if boxes is not None and boxes.xyxy is not None and len(boxes) > 0:
            xyxy = boxes.xyxy.cpu().numpy()
            confs = boxes.conf.cpu().numpy()
            clss = boxes.cls.cpu().numpy().astype(int)
            if boxes.id is not None:
                ids = boxes.id.cpu().numpy().astype(int)
            else:
                ids = np.array([None] * len(xyxy), dtype=object)
            for (x1, y1, x2, y2), c, cl, tid in zip(xyxy, confs, clss, ids):
                tracks.append(
                    Track(
                        track_id=(int(tid) if tid is not None else None),
                        cls=int(cl),
                        conf=float(c),
                        x1=int(x1), y1=int(y1), x2=int(x2), y2=int(y2),
                    )
                )

        self._last = tracks
        return tracks
