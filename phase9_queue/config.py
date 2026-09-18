"""Phase 9 configuration. Unlike Phase 8 (shelf monitoring), this needs no
camera or model of its own - it reuses the main pipeline's already-running
person tracker via `VisionPipeline.frame_hooks`, so the only setting is how
often to check occupancy against capacity.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _get_float(name: str, default: float) -> float:
    v = os.getenv(name)
    try:
        return float(v) if v not in (None, "") else default
    except ValueError:
        return default


@dataclass
class QueueConfig:
    check_interval_s: float   # how often to recount occupancy per zone
    margin: float             # tolerance grown outward from each zone, same idea as Phase 7

    @classmethod
    def load(cls) -> "QueueConfig":
        return cls(
            check_interval_s=_get_float("QUEUE_CHECK_INTERVAL_S", 5.0),
            margin=_get_float("QUEUE_ZONE_MARGIN", 0.08),
        )
