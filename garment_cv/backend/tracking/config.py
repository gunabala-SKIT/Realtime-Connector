"""Tracking settings (Phase 3, spec section 9 -> PLAN.md).

Why this is a config module, not a `Tracker` class of its own: Ultralytics implements tracking
as a *mode* of the same detection model - `model.track(persist=True, tracker=...)` instead of
`model.predict(...)` - carrying state across calls on that one model object, not as an
independent stage you bolt on after detection. A separate `ObjectTracker` class calling `.track()`
on its own model would either load the weights a second time for no benefit, or fight over the
same model object's internal tracker state with `detection.ObjectDetector`. So `ObjectDetector`
(in `backend/detection/detector.py`) is what actually calls `.track()` when this is enabled - see
its docstring. This module only holds the tracker choice and its own settings, so they're
configured and tested on their own, per the spec's project structure (section 24).
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import _get, ensure_env_loaded

VALID_TRACKERS = ("bytetrack.yaml", "botsort.yaml")


def _get_bool(name: str, default: bool) -> bool:
    v = _get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class TrackingConfig:
    enabled: bool
    tracker: str   # "bytetrack.yaml" (lighter, no re-identification) or "botsort.yaml" (adds
                   # camera-motion compensation and appearance re-identification - the default,
                   # matching this repo's other YOLO-tracking module, phase2_detect)

    @classmethod
    def load(cls) -> "TrackingConfig":
        ensure_env_loaded()
        tracker = (_get("GARMENT_TRACKER", "botsort.yaml") or "botsort.yaml").strip().lower()
        if tracker not in VALID_TRACKERS:
            tracker = "botsort.yaml"
        return cls(enabled=_get_bool("GARMENT_TRACK_ENABLED", True), tracker=tracker)
