"""Phase 2 configuration.

Reuses the Phase 1 camera settings (same ``.env``, same ``CameraStream``) and adds
the detection / tracking parameters.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
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


def _get_bool(name: str, default: bool) -> bool:
    v = _get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Phase2Config:
    camera: CameraConfig
    model: str
    device: str
    conf: float
    iou: float
    imgsz: int
    classes: list[int]
    tracker: str
    process_every_n: int
    half: bool
    log_level: str
    log_dir: Path

    @classmethod
    def load(cls, env_file: str | os.PathLike | None = None) -> "Phase2Config":
        camera = CameraConfig.load(env_file)  # this call runs load_dotenv()

        classes_raw = _get("YOLO_CLASSES", "0") or "0"
        classes = [int(x) for x in classes_raw.replace(" ", "").split(",") if x != ""]

        log_dir = Path(_get("LOG_DIR", "logs"))
        if not log_dir.is_absolute():
            log_dir = BASE_DIR / log_dir

        tracker = _get("TRACKER", "botsort.yaml") or "botsort.yaml"
        if tracker not in ("botsort.yaml", "bytetrack.yaml"):
            tracker = "botsort.yaml"

        return cls(
            camera=camera,
            model=_get("YOLO_MODEL", "yolo11n.pt") or "yolo11n.pt",
            device=_get("YOLO_DEVICE", "") or "",
            conf=_get_float("YOLO_CONF", 0.35),
            iou=_get_float("YOLO_IOU", 0.5),
            imgsz=_get_int("YOLO_IMGSZ", 640),
            classes=classes or [0],
            tracker=tracker,
            process_every_n=max(1, _get_int("PROCESS_EVERY_N", 1)),
            half=_get_bool("YOLO_HALF", False),
            log_level=(_get("LOG_LEVEL", "INFO") or "INFO").upper(),
            log_dir=log_dir,
        )
