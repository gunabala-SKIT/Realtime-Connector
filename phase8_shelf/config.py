"""Phase 8 configuration - a second, independent camera (SHELF_ prefix) plus
the shelf-checker's own settings. Shares the events.db used by Phases 4-7 so
shelf data lives alongside person events without a second database.
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
class Phase8Config:
    camera: CameraConfig      # loaded with prefix "SHELF_" - independent of the main camera
    camera_id: str
    model: str
    device: str
    conf: float
    imgsz: int
    classes: list[int]        # COCO class ids to count; default 39 = "bottle"
    check_interval_s: float   # how often to sample + count (shelves don't need 15fps)
    events_db: Path           # same DB the dashboard reads (phase4_events)
    snapshot_dir: Path
    log_level: str
    log_dir: Path

    @classmethod
    def load(cls, env_file: str | os.PathLike | None = None) -> "Phase8Config":
        camera = CameraConfig.load(env_file, prefix="SHELF_")

        # Reuse the same events DB / snapshot dir as the main pipeline by default,
        # so shelf alerts show up in the one dashboard - override if you want a
        # fully separate deployment.
        phase4_dir = (BASE_DIR.parent / "phase4_events").resolve()
        events_db = _resolve(_get("EVENTS_DB", str(phase4_dir / "events.db")), phase4_dir)
        snapshot_dir = _resolve(_get("SNAPSHOT_DIR", str(phase4_dir / "snapshots")), phase4_dir)

        log_dir = Path(_get("SHELF_LOG_DIR", "logs"))
        if not log_dir.is_absolute():
            log_dir = BASE_DIR / log_dir

        classes_raw = _get("SHELF_YOLO_CLASSES", "39") or "39"  # 39 = COCO "bottle"
        classes = [int(x) for x in classes_raw.replace(" ", "").split(",") if x != ""]

        return cls(
            camera=camera,
            camera_id=_get("SHELF_CAMERA_ID", "shelf-cam-1") or "shelf-cam-1",
            model=_get("SHELF_YOLO_MODEL", "yolo11s.pt") or "yolo11s.pt",
            device=_get("SHELF_YOLO_DEVICE", "") or "",
            conf=_get_float("SHELF_YOLO_CONF", 0.35),
            imgsz=_get_int("SHELF_YOLO_IMGSZ", 960),
            classes=classes or [39],
            check_interval_s=_get_float("SHELF_CHECK_INTERVAL_S", 30.0),
            events_db=events_db,
            snapshot_dir=snapshot_dir,
            log_level=(_get("SHELF_LOG_LEVEL", "INFO") or "INFO").upper(),
            log_dir=log_dir,
        )
