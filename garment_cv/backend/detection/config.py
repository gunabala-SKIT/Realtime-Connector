"""Detection settings (Phase 2, spec section 26 -> PLAN.md). All from environment variables -
nothing hard-coded, same rule as the camera module."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..config import _get, _get_int, ensure_env_loaded


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


def _parse_class_conf(raw: str | None) -> dict[str, float]:
    """"scissors=0.10,fabric_piece=0.2" -> {"scissors": 0.10, "fabric_piece": 0.2}. A malformed entry
    is skipped rather than failing startup - the global threshold still applies to that class."""
    out: dict[str, float] = {}
    for part in (raw or "").split(","):
        name, _, value = part.partition("=")
        try:
            out[name.strip()] = float(value)
        except ValueError:
            continue
    out.pop("", None)
    return out


@dataclass
class DetectionConfig:
    enabled: bool
    model: str
    device: str              # "" = auto (GPU if available, else CPU)
    conf: float
    iou: float
    imgsz: int
    half: bool                # keep False on GTX 16-series cards - see README
    detect_interval_s: float  # minimum time between inference runs, so detection can never
                              # become the bottleneck on the frame-capture thread
    stream_max_width: int
    jpeg_quality: int
    class_conf: dict[str, float] = field(default_factory=dict)  # per-class override of `conf`, by the
                              # class name the detector reports - e.g. a small, often half-hidden tool
                              # the model finds at low confidence needs a lower bar than a person does

    @classmethod
    def load(cls) -> "DetectionConfig":
        ensure_env_loaded()
        return cls(
            enabled=_get_bool("GARMENT_DETECT_ENABLED", True),
            model=_get("GARMENT_DETECT_MODEL", "yolo11n.pt") or "yolo11n.pt",
            device=_get("GARMENT_DETECT_DEVICE", "") or "",
            conf=_get_float("GARMENT_DETECT_CONF", 0.35),
            iou=_get_float("GARMENT_DETECT_IOU", 0.5),
            imgsz=_get_int("GARMENT_DETECT_IMGSZ", 640),
            half=_get_bool("GARMENT_DETECT_HALF", False),
            detect_interval_s=_get_float("GARMENT_DETECT_INTERVAL_S", 0.3),
            stream_max_width=_get_int("GARMENT_DETECT_STREAM_MAX_WIDTH", 960),
            jpeg_quality=max(10, min(100, _get_int("GARMENT_DETECT_JPEG_QUALITY", 80))),
            class_conf=_parse_class_conf(_get("GARMENT_DETECT_CLASS_CONF")),
        )
