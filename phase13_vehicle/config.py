"""Phase 13 configuration - a second, independent camera (VEHICLE_ prefix),
same pattern as Phase 8's shelf camera. Shares the events.db so vehicle
data lives alongside person events without a second database.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from phase1_camera.config import Config as CameraConfig

BASE_DIR = Path(__file__).resolve().parent


def _get(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    return v if v not in (None, "") else default


def _get_int(name: str, default: int) -> int:
    try:
        return int(_get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _get_float(name: str, default: float) -> float:
    try:
        return float(_get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _resolve(p: str, base: Path) -> Path:
    path = Path(p)
    return path if path.is_absolute() else base / path


@dataclass
class Phase13Config:
    camera: CameraConfig      # loaded with prefix "VEHICLE_" - independent of the main camera
    camera_id: str
    model: str
    device: str
    conf: float
    iou: float
    imgsz: int
    classes: list[int]        # COCO vehicle classes: 2=car 3=motorcycle 5=bus 7=truck
    tracker: str
    half: bool
    ocr_interval_s: float     # min seconds between OCR attempts on the SAME unconfirmed track
    min_readings: int         # readings needed before committing a majority-vote plate
    min_ocr_conf: float       # per-reading EasyOCR confidence floor to even consider a candidate
    stale_after_s: float      # forget a track's OCR buffer this long after it's not seen
    events_db: Path
    snapshot_dir: Path
    log_level: str
    log_dir: Path

    @classmethod
    def load(cls, env_file: str | os.PathLike | None = None) -> "Phase13Config":
        camera = CameraConfig.load(env_file, prefix="VEHICLE_")

        phase4_dir = (BASE_DIR.parent / "phase4_events").resolve()
        events_db = _resolve(_get("EVENTS_DB", str(phase4_dir / "events.db")), phase4_dir)
        snapshot_dir = _resolve(_get("SNAPSHOT_DIR", str(phase4_dir / "snapshots")), phase4_dir)

        log_dir = Path(_get("VEHICLE_LOG_DIR", "logs"))
        if not log_dir.is_absolute():
            log_dir = BASE_DIR / log_dir

        classes_raw = _get("VEHICLE_YOLO_CLASSES", "2,3,5,7") or "2,3,5,7"
        classes = [int(x) for x in classes_raw.replace(" ", "").split(",") if x != ""]

        tracker = _get("VEHICLE_TRACKER", "botsort.yaml") or "botsort.yaml"
        if tracker not in ("botsort.yaml", "bytetrack.yaml"):
            tracker = "botsort.yaml"

        return cls(
            camera=camera,
            camera_id=_get("VEHICLE_CAMERA_ID", "vehicle-cam-1") or "vehicle-cam-1",
            model=_get("VEHICLE_YOLO_MODEL", "yolo11s.pt") or "yolo11s.pt",
            device=_get("VEHICLE_YOLO_DEVICE", "") or "",
            conf=_get_float("VEHICLE_YOLO_CONF", 0.4),
            iou=_get_float("VEHICLE_YOLO_IOU", 0.5),
            imgsz=_get_int("VEHICLE_YOLO_IMGSZ", 960),
            classes=classes or [2, 3, 5, 7],
            tracker=tracker,
            half=False,
            ocr_interval_s=_get_float("VEHICLE_OCR_INTERVAL_S", 1.5),
            min_readings=_get_int("VEHICLE_MIN_READINGS", 3),
            min_ocr_conf=_get_float("VEHICLE_MIN_OCR_CONF", 0.3),
            stale_after_s=_get_float("VEHICLE_STALE_S", 30.0),
            events_db=events_db,
            snapshot_dir=snapshot_dir,
            log_level=(_get("VEHICLE_LOG_LEVEL", "INFO") or "INFO").upper(),
            log_dir=log_dir,
        )
