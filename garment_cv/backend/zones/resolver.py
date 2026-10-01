"""Answers spec section 10's actual question: "is this tracked person/object inside a configured
zone?" - for one box at a time, so callers (the detector, tests) don't need to know anything
about `Zone` or `ZoneStore` beyond these methods.
"""
from __future__ import annotations

from .geometry import ground_point, point_in_polygon
from .store import Zone, ZoneStore


class ZoneResolver:
    def __init__(self, store: ZoneStore) -> None:
        self.store = store

    def _zone_at(self, camera_id: str, box: tuple[float, float, float, float],
                frame_w: int, frame_h: int) -> Zone | None:
        """The first configured zone (on this camera) whose polygon contains the box's ground
        point, or None. If zones overlap, the one added earliest (lowest id) wins -
        `ZoneStore.list_zones` returns them in that order."""
        gx, gy = ground_point(box, frame_w, frame_h)
        for z in self.store.list_zones(camera_id):
            if point_in_polygon(gx, gy, z.points):
                return z
        return None

    def zone_for_box(self, camera_id: str, box: tuple[float, float, float, float],
                     frame_w: int, frame_h: int) -> str | None:
        """The zone's name, for display."""
        z = self._zone_at(camera_id, box, frame_w, frame_h)
        return z.name if z else None

    def activity_for_box(self, camera_id: str, box: tuple[float, float, float, float],
                         frame_w: int, frame_h: int) -> str | None:
        """The zone's configured activity type (spec section 10's zone purpose - "cutting",
        "marking", ...), for the Phase 5 activity engine to key its rules on. None if the box is
        in no zone, or in a zone with no activity type set."""
        z = self._zone_at(camera_id, box, frame_w, frame_h)
        return z.activity if z else None

    def zones_for_drawing(self, camera_id: str) -> list[tuple[str, list[list[float]]]]:
        """(name, points) for every zone on this camera - for drawing outlines on the live view,
        not for membership testing (use `zone_for_box`/`activity_for_box` for that)."""
        return [(z.name, z.points) for z in self.store.list_zones(camera_id)]
