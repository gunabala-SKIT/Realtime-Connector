"""Phase 14 configuration (read from phase1_camera/.env like every phase).

PPE runs inside the dashboard process off the main pipeline's person tracker,
so the only extra pieces are its own detector model and the check schedule.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from phase4_events.store import PPE_ITEMS as ITEMS

HERE = Path(__file__).resolve().parent


def _get(name: str, default: str) -> str:
    v = os.getenv(name)
    return v if v not in (None, "") else default


def _get_float(name: str, default: float) -> float:
    try:
        return float(_get(name, str(default)))
    except ValueError:
        return default


def _get_int(name: str, default: int) -> int:
    try:
        return int(_get(name, str(default)))
    except ValueError:
        return default


def parse_items(value: str | None) -> list[str]:
    """'helmet, vest' -> ['helmet', 'vest'], dropping unknown names and duplicates."""
    out: list[str] = []
    for part in (value or "").split(","):
        item = part.strip().lower()
        if item in ITEMS and item not in out:
            out.append(item)
    return out


@dataclass
class PPEConfig:
    model: str                 # trained by phase14_ppe/train.py
    device: str                # "" = CUDA if available
    conf: float                # PPE detector confidence threshold
    imgsz: int
    check_interval_s: float    # how often to run a compliance check
    confirm_checks: int        # consecutive failing checks before a violation is logged
    cooldown_s: float          # min seconds before the same person/item is logged again
    min_person_px: int         # people shorter than this are too far away to judge
    default_items: list[str]   # pre-filled when marking a new zone as a PPE zone
    zone_margin: float         # same "forgive rough drawing" tolerance as Phase 7 zones

    @classmethod
    def load(cls) -> "PPEConfig":
        model = Path(_get("PPE_MODEL", str(HERE / "models" / "ppe.pt")))
        if not model.is_absolute():
            model = HERE.parent / model
        return cls(
            model=str(model),
            device=_get("PPE_DEVICE", ""),
            conf=_get_float("PPE_CONF", 0.4),
            imgsz=_get_int("PPE_IMGSZ", 640),
            check_interval_s=_get_float("PPE_CHECK_INTERVAL_S", 1.0),
            confirm_checks=max(1, _get_int("PPE_CONFIRM_CHECKS", 3)),
            cooldown_s=_get_float("PPE_COOLDOWN_S", 300.0),
            min_person_px=_get_int("PPE_MIN_PERSON_PX", 80),
            default_items=parse_items(_get("PPE_REQUIRED_ITEMS", "helmet,vest,gloves")) or ["helmet", "vest"],
            zone_margin=_get_float("PPE_ZONE_MARGIN", 0.08),
        )
