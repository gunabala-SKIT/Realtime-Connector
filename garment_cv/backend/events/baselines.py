"""Bottleneck baselines (spec section 18): an administrator-configured expected duration per
stage, compared against the measured average. The system only ever reports the comparison and
flags "Needs Investigation" - it never guesses *why* a stage is slow (spec section 18 lists
possible causes - pattern complexity, fabric type, worker experience, a machine issue, material
waiting, rework, poor camera visibility - as things a supervisor investigates, explicitly not
something this system decides on its own).

A stage with no baseline configured is left out of the bottleneck list entirely, not silently
compared against some made-up default - there is no data-driven way to invent an expected
duration, and a fabricated one would be worse than no comparison at all.
"""
from __future__ import annotations

from ..config import _get, ensure_env_loaded

_ENV_KEYS = {
    "MARKING": "GARMENT_BASELINE_MARKING_S",
    "CUTTING": "GARMENT_BASELINE_CUTTING_S",
    "STITCHING": "GARMENT_BASELINE_STITCHING_S",
    "PIECE_HANDLING": "GARMENT_BASELINE_PIECE_HANDLING_S",
}


def load_baselines() -> dict[str, float | None]:
    ensure_env_loaded()
    out: dict[str, float | None] = {}
    for stage, key in _ENV_KEYS.items():
        v = _get(key)
        if v is None:
            out[stage] = None
            continue
        try:
            out[stage] = float(v)
        except ValueError:
            out[stage] = None
    return out
