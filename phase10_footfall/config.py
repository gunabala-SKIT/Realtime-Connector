"""Phase 10 configuration. Like Phase 9, this needs no camera or model of
its own - it taps the main pipeline's already-running person tracker via
`VisionPipeline.frame_hooks`.
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
class FootfallConfig:
    margin: float               # normalised distance from the line that counts as "clearly on a side"
    confirm_frames: int         # consecutive frames a track must hold the new side before it counts
    recross_cooldown_s: float   # min seconds between two counted crossings by the SAME track on
                                 # the SAME line - a hard backstop independent of margin/confirm_frames
                                 # tuning, since a real person can't walk back through a doorway
                                 # within ~1-2s of just having walked through it, but someone
                                 # standing/swaying right at the line can still occasionally hold
                                 # a "wrong" side for confirm_frames in a row by chance
    stale_after_s: float        # forget a track's side-of-line state this long after it's not seen

    @classmethod
    def load(cls) -> "FootfallConfig":
        return cls(
            margin=_get_float("FOOTFALL_LINE_MARGIN", 0.03),
            confirm_frames=int(_get_float("FOOTFALL_CONFIRM_FRAMES", 4)),
            recross_cooldown_s=_get_float("FOOTFALL_RECROSS_COOLDOWN_S", 2.0),
            stale_after_s=_get_float("FOOTFALL_STALE_S", 30.0),
        )
