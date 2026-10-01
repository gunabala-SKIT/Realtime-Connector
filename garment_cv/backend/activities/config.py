"""Activity engine thresholds (Phase 5, spec section 11: "the rule engine must be configurable
rather than hard-coded wherever possible"). Every duration, movement threshold and attribution
margin below is a real, tunable knob - nothing about "how long is long enough" or "how much
movement counts as movement" is baked into the code.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import _get, ensure_env_loaded


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


def _get_int(name: str, default: int) -> int:
    try:
        return int(_get(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass
class ActivityConfig:
    enabled: bool
    cutting_min_s: float
    marking_min_s: float
    stitching_min_s: float
    piece_handling_min_s: float
    idle_min_s: float
    grace_s: float                    # how long a condition may drop out before its streak resets -
                                      # without this, one missed detection frame (occlusion, a hand
                                      # briefly blocking the view) would restart every timer from zero
    movement_window_s: float          # how far back to look when judging "is this object moving"
    movement_min_fraction: float      # minimum displacement within that window, as a fraction of the
                                      # frame's diagonal, to count as movement rather than noise/jitter
    attribution_margin: float         # how far a tool's box may sit outside a person's own box (as a
                                      # fraction of the person's box size) and still count as theirs -
                                      # a hand holding scissors extends past the body outline
    max_history: int                  # completed activities also kept in memory, capped, for fast
                                      # no-DB-round-trip access - the database (Phase 6) is the
                                      # durable copy; this cap only bounds the in-memory one

    @classmethod
    def load(cls) -> "ActivityConfig":
        ensure_env_loaded()
        return cls(
            enabled=_get_bool("GARMENT_ACTIVITY_ENABLED", True),
            cutting_min_s=_get_float("GARMENT_ACTIVITY_CUTTING_MIN_S", 3.0),
            marking_min_s=_get_float("GARMENT_ACTIVITY_MARKING_MIN_S", 3.0),
            stitching_min_s=_get_float("GARMENT_ACTIVITY_STITCHING_MIN_S", 5.0),
            piece_handling_min_s=_get_float("GARMENT_ACTIVITY_PIECE_HANDLING_MIN_S", 2.0),
            idle_min_s=_get_float("GARMENT_ACTIVITY_IDLE_MIN_S", 10.0),
            grace_s=_get_float("GARMENT_ACTIVITY_GRACE_S", 1.5),
            movement_window_s=_get_float("GARMENT_ACTIVITY_MOVEMENT_WINDOW_S", 2.0),
            movement_min_fraction=_get_float("GARMENT_ACTIVITY_MOVEMENT_MIN_FRACTION", 0.03),
            attribution_margin=_get_float("GARMENT_ACTIVITY_ATTRIBUTION_MARGIN", 0.3),
            max_history=_get_int("GARMENT_ACTIVITY_MAX_HISTORY", 200),
        )
