"""Object detection (Phase 2) + tracking (Phase 3) + zone membership (Phase 4). Plugs into the
camera module through the hook seam Phase 1 built for exactly this - `CameraRunner.frame_hooks` -
with no changes to that module.

Detection and tracking live in the same class because Ultralytics implements tracking as a mode
of the same model call (`model.track()` vs `model.predict()`), not an independent stage - see
`tracking/config.py`'s docstring for why. Zones are different: checking whether a box's ground
point falls inside a polygon needs nothing from the model, so that logic lives entirely in
`backend/zones/` (geometry + storage, independently testable, no camera or model code at all) -
this class only asks it one question per detection, through the small `_ZoneLookup` protocol
below, so it never needs to import anything about how zones are stored or drawn.

No activity logic (Phase 5) here: this only answers "what objects are visible, which one is the
same object as last frame, and which zone (if any) they're standing in", using a stock
COCO-pretrained model as a placeholder until real garment-factory training data exists (spec
section 7). See `coco_standin.py` for exactly which of the spec's required classes that can and
can't stand in for.

A model whose classes don't overlap with that COCO stand-in vocabulary at all (e.g. a fully
custom-trained model - see `garment_cv/training/`) is a genuinely different situation from a
misconfigured one, not treated as an error: `start()` falls back to using every class *that* model
offers, instead of refusing to enable detection. `status()`'s `detected_classes`/
`not_yet_detectable` reflect whichever mode actually loaded.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from typing import Protocol

import cv2
import numpy as np

from ..tracking.config import TrackingConfig
from .coco_standin import COCO_TO_GARMENT, NOT_YET_DETECTABLE
from .config import DetectionConfig

_BOX_COLOR = (60, 170, 60)
_ZONE_COLOR = (40, 130, 240)


class _ZoneLookup(Protocol):
    def zone_for_box(self, camera_id: str, box: tuple[float, float, float, float],
                     frame_w: int, frame_h: int) -> str | None: ...

    def activity_for_box(self, camera_id: str, box: tuple[float, float, float, float],
                         frame_w: int, frame_h: int) -> str | None: ...

    def zones_for_drawing(self, camera_id: str) -> list[tuple[str, list[list[float]]]]: ...


class _ActivityLookup(Protocol):
    def update(self, camera_id: str, detections: list["Detection"], ts: float,
              frame_w: int, frame_h: int) -> dict[int, str | None]: ...


@dataclass(frozen=True)
class Detection:
    cls: str          # this product's vocabulary name, e.g. "person"
    coco_name: str     # what the underlying COCO model actually called it, e.g. "person"
    conf: float
    box: tuple[float, float, float, float]  # x1, y1, x2, y2 in the frame's own pixels
    track_id: int | None = None  # stable across frames while tracking keeps sight of this object;
                                 # None when tracking is off, or briefly while a new track is unconfirmed
    zone: str | None = None      # name of the configured zone this object's ground point falls
                                 # inside, or None if it's in no zone (or none are configured)
    zone_activity: str | None = None  # that zone's configured activity type ("cutting", ...),
                                      # for the Phase 5 activity engine - None if the zone (or the
                                      # box's ground point) has no activity type set


class ObjectDetector:
    def __init__(self, cfg: DetectionConfig, logger, tracking: TrackingConfig | None = None,
                zones: _ZoneLookup | None = None, activities: _ActivityLookup | None = None) -> None:
        self.cfg = cfg
        self.log = logger
        self.tracking = tracking or TrackingConfig(enabled=False, tracker="botsort.yaml")
        self.zones = zones
        self.activities = activities
        self.model = None
        self.names: dict[int, str] = {}
        self._wanted_ids: list[int] = []
        self._custom_model = False  # True once `start()` loads a model with no COCO stand-in overlap
        self.device = "cpu"
        # ultralytics deprecated the `half=` predict() argument in favour of `quantize=` (16 ==
        # the old half=True, None == FP32/half=False); keep the config's own field named `half`
        # since that's the well-known term used the same way elsewhere in this repo (YOLO_HALF).
        self._quantize = 16 if cfg.half else None
        self.enabled = False
        self.disabled_reason: str | None = "not started"

        self._lock = threading.Lock()
        self._detections: list[Detection] = []
        self._annotated_jpeg: bytes | None = None
        self._ts = 0.0
        self._last_run = 0.0
        self.infer_ms = 0.0
        self.infer_count = 0

    # ------------------------------------------------------------------- setup
    def start(self) -> None:
        """Loads the model. Not done in __init__ so a disabled/misconfigured detector never
        pays model-load cost, and so tests can construct the object cheaply before deciding
        whether to load a real model."""
        if not self.cfg.enabled:
            self.disabled_reason = "disabled (GARMENT_DETECT_ENABLED=false)"
            self.log.info("Object detection disabled - %s", self.disabled_reason)
            return
        import torch
        from ultralytics import YOLO

        model = YOLO(self.cfg.model)
        self.names = {int(k): v for k, v in model.names.items()}
        coco_ids = sorted(i for i, n in self.names.items() if n in COCO_TO_GARMENT)
        # A model with zero overlap with the COCO stand-in vocabulary isn't misconfigured - it's a
        # genuinely different (e.g. fully custom-trained) model, so fall back to using everything
        # it offers rather than refusing to enable detection at all. Overlap alone isn't enough to
        # call it the COCO stand-in, though: a garment-trained model also knows `person` and
        # `scissors`, and filtering it down to just those would hide the very classes it was
        # trained for. If the model itself produces a garment class COCO has no equivalent for
        # (fabric, marking_tool, ...), it is a garment model and every class is used.
        has_garment_classes = any(n in NOT_YET_DETECTABLE for n in self.names.values())
        self._custom_model = (not coco_ids) or has_garment_classes
        self._wanted_ids = coco_ids if not self._custom_model else sorted(self.names.keys())
        if not self._wanted_ids:
            self.disabled_reason = f"{self.cfg.model} reports zero classes"
            self.log.warning("Object detection disabled - %s", self.disabled_reason)
            return
        self.device = self.cfg.device or (0 if torch.cuda.is_available() else "cpu")
        blank = np.zeros((self.cfg.imgsz, self.cfg.imgsz, 3), np.uint8)
        if self.tracking.enabled:
            model.track(blank, persist=False, tracker=self.tracking.tracker, imgsz=self.cfg.imgsz,
                       device=self.device, quantize=self._quantize, verbose=False)  # warm-up
        else:
            model.predict(blank, imgsz=self.cfg.imgsz, device=self.device, quantize=self._quantize,
                          verbose=False)  # warm-up
        self.model = model
        self.enabled, self.disabled_reason = True, None
        # Tell the activity engine which classes this model can actually produce, so it can tell
        # "no cloth in this frame" from "this model can't see cloth at all". Duck-typed and
        # optional: the engine is only reached through the small `_ActivityLookup` protocol.
        configure = getattr(self.activities, "configure", None)
        if configure is not None:
            configure(self.names.values() if self._custom_model else COCO_TO_GARMENT.values())
        if self._custom_model:
            self.log.info("Object detection ready. model=%s device=%s classes=%s tracker=%s (custom "
                          "model - not the COCO stand-in)", self.cfg.model, self.device,
                          sorted(self.names.values()), self.tracking.tracker if self.tracking.enabled else "off")
        else:
            self.log.info("Object detection ready. model=%s device=%s classes=%s tracker=%s (%s not yet "
                          "detectable - see coco_standin.py)", self.cfg.model, self.device,
                          sorted(COCO_TO_GARMENT.values()), self.tracking.tracker if self.tracking.enabled else "off",
                          len(NOT_YET_DETECTABLE))

    # --------------------------------------------------------------------- hook
    def observe(self, camera_id: str, frame, ts: float) -> None:
        """`CameraRunner.frame_hooks` callback: `hook(camera_id, frame, ts)`. Throttled to
        `detect_interval_s` so detection can never become the bottleneck on the capture thread."""
        if not self.enabled or ts - self._last_run < self.cfg.detect_interval_s:
            return
        self._last_run = ts
        dets = self.detect(frame)
        h, w = frame.shape[:2]
        dets = self._with_zones(camera_id, dets, w, h)
        current_activity = self.activities.update(camera_id, dets, ts, w, h) if self.activities is not None else {}
        annotated = self._annotate(frame.copy(), dets, camera_id, current_activity)
        with self._lock:
            self._detections, self._ts, self._annotated_jpeg = dets, ts, annotated

    def _with_zones(self, camera_id: str, dets: list[Detection], frame_w: int, frame_h: int) -> list[Detection]:
        """Pure post-processing of already-computed detections - no model involved, so this is
        tested directly with hand-built `Detection`s (`test_detection_zones.py`) as well as
        end-to-end through `observe()`. Only rewrites `zone`/`zone_activity`; box/track_id/etc.
        are untouched."""
        if self.zones is None:
            return dets
        return [replace(d, zone=self.zones.zone_for_box(camera_id, d.box, frame_w, frame_h),
                        zone_activity=self.zones.activity_for_box(camera_id, d.box, frame_w, frame_h))
               for d in dets]

    def detect(self, frame) -> list[Detection]:
        """Runs the model on one frame. Also used directly by tests and by any future offline
        evaluation, without going through the camera/hook machinery.

        When tracking is on, this calls `model.track(persist=True, ...)` instead of
        `model.predict(...)` - the tracker's state lives on `self.model` and is carried forward
        by `persist=True` from one call to the next, so `detect()` must be called on frames *in
        order* for IDs to mean anything (exactly as it already is, from `observe()`)."""
        t0 = time.perf_counter()
        # The model is asked for everything above the LOWEST threshold in play; each class is then held
        # to its own bar below. A class's override can only be looser than `conf` or stricter - either
        # way the model must be run at the minimum so a looser override isn't cut off before it's seen.
        floor = min([self.cfg.conf, *self.cfg.class_conf.values()])
        common = dict(imgsz=self.cfg.imgsz, conf=floor, iou=self.cfg.iou, classes=self._wanted_ids,
                     device=self.device, quantize=self._quantize, verbose=False)
        if self.tracking.enabled:
            r = self.model.track(frame, persist=True, tracker=self.tracking.tracker, **common)[0]
        else:
            r = self.model.predict(frame, **common)[0]
        self.infer_ms = (time.perf_counter() - t0) * 1000
        self.infer_count += 1
        out = []
        boxes = getattr(r, "boxes", None)
        if boxes is not None and boxes.xyxy is not None and len(boxes) > 0:
            ids = boxes.id.int().tolist() if boxes.id is not None else [None] * len(boxes)
            for b, c, p, tid in zip(boxes.xyxy.tolist(), boxes.cls.tolist(), boxes.conf.tolist(), ids):
                coco_name = self.names[int(c)]
                cls_name = COCO_TO_GARMENT.get(coco_name, coco_name)
                if float(p) < self.cfg.class_conf.get(cls_name, self.cfg.conf):
                    continue
                # .get(..., coco_name): in custom-model mode this name isn't a COCO class at all,
                # so it passes through unchanged - in COCO stand-in mode `_wanted_ids` already
                # guarantees every name here is a COCO_TO_GARMENT key, so this is unchanged there.
                out.append(Detection(cls=COCO_TO_GARMENT.get(coco_name, coco_name), coco_name=coco_name,
                                     conf=float(p), box=tuple(float(v) for v in b), track_id=tid))
        return out

    def _annotate(self, frame, dets: list[Detection], camera_id: str | None = None,
                 current_activity: dict[int, str | None] | None = None) -> bytes:
        current_activity = current_activity or {}
        if self.zones is not None and camera_id is not None:
            h, w = frame.shape[:2]
            for name, points in self.zones.zones_for_drawing(camera_id):
                pts = np.array([[int(x * w), int(y * h)] for x, y in points], np.int32)
                cv2.polylines(frame, [pts], True, _ZONE_COLOR, 2)
                x0, y0 = pts[0]
                cv2.putText(frame, name, (int(x0), max(int(y0) - 6, 12)), cv2.FONT_HERSHEY_SIMPLEX,
                           0.5, _ZONE_COLOR, 1, cv2.LINE_AA)
        for d in dets:
            x1, y1, x2, y2 = (int(v) for v in d.box)
            cv2.rectangle(frame, (x1, y1), (x2, y2), _BOX_COLOR, 2)
            label = f"{d.cls} #{d.track_id}" if d.track_id is not None else d.cls
            label += f" {d.conf:.2f}" + (f" [{d.zone}]" if d.zone else "")
            activity = current_activity.get(d.track_id) if d.track_id is not None else None
            if activity:
                label += f" {activity}"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(frame, (x1, max(y1 - th - 8, 0)), (x1 + tw + 6, y1), _BOX_COLOR, -1)
            cv2.putText(frame, label, (x1 + 3, max(y1 - 5, 12)), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                       (255, 255, 255), 1, cv2.LINE_AA)
        if self.cfg.stream_max_width and frame.shape[1] > self.cfg.stream_max_width:
            scale = self.cfg.stream_max_width / frame.shape[1]
            frame = cv2.resize(frame, (self.cfg.stream_max_width, int(frame.shape[0] * scale)),
                               interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.cfg.jpeg_quality])
        return buf.tobytes() if ok else b""

    # -------------------------------------------------------------- introspection
    def latest_jpeg(self) -> bytes | None:
        """Same name/shape as `CameraRunner.latest_jpeg` on purpose: `mjpeg_chunks` in
        `backend/api/routes.py` is duck-typed against any object with this method, so the same
        generator serves both the raw and the annotated live stream."""
        with self._lock:
            return self._annotated_jpeg

    def status(self) -> dict:
        with self._lock:
            dets, ts = self._detections, self._ts
        if self._custom_model:
            detected_classes, not_yet_detectable = sorted(self.names.values()), []
        else:
            detected_classes, not_yet_detectable = sorted(COCO_TO_GARMENT.values()), list(NOT_YET_DETECTABLE)
        return {
            "enabled": self.enabled, "disabled_reason": self.disabled_reason, "model": self.cfg.model,
            "custom_model": self._custom_model,
            "device": str(self.device), "infer_ms": round(self.infer_ms, 1), "infer_count": self.infer_count,
            "detected_classes": detected_classes, "not_yet_detectable": not_yet_detectable,
            "tracking_enabled": self.tracking.enabled, "tracker": self.tracking.tracker if self.tracking.enabled else None,
            "ts": ts or None,
            "detections": [{"class": d.cls, "coco_class": d.coco_name, "confidence": round(d.conf, 3),
                            "box": [round(v, 1) for v in d.box], "track_id": d.track_id, "zone": d.zone,
                            "zone_activity": d.zone_activity} for d in dets],
        }
