"""Resolves which zone(s) a track's position falls in, cached briefly so the
vision loop doesn't hit SQLite every frame (zones change rarely).
"""
from __future__ import annotations

import time

from phase4_events.store import EventStore

from .geometry import ground_point, point_in_polygon


class ZoneIndex:
    def __init__(self, store: EventStore, refresh_interval_s: float = 5.0) -> None:
        self.store = store
        self.refresh_interval_s = refresh_interval_s
        self._by_camera: dict[str, list] = {}
        self._last_refresh = 0.0

    def _refresh(self) -> None:
        now = time.time()
        if now - self._last_refresh < self.refresh_interval_s:
            return
        self._last_refresh = now
        by_camera: dict[str, list] = {}
        for z in self.store.list_zones(enabled_only=True):
            by_camera.setdefault(z.camera_id, []).append(z)
        self._by_camera = by_camera

    def resolve(self, camera_id: str, bbox: tuple[int, int, int, int],
                frame_w: int, frame_h: int) -> str | None:
        """Comma-joined names of every zone containing this box's ground point,
        or None if it's in no zone (or no zones are configured for this camera)."""
        self._refresh()
        zones = self._by_camera.get(camera_id)
        if not zones:
            return None
        pt = ground_point(bbox, frame_w, frame_h)
        hits = [z.name for z in zones if point_in_polygon(pt, z.points)]
        return ",".join(sorted(hits)) if hits else None
