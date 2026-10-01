"""Zone storage (Phase 4). No database existed yet when this was built (Phase 6 added one later),
so zones are persisted to a small JSON file - one write-through in-memory list, not a cache that
can go stale, since this one `ZoneStore` instance is shared by whatever reads zones (the resolver)
and whatever writes them (the API) within this single process. Left on JSON even after Phase 6,
since nothing since has needed zones to live in the database instead.

A zone's points are normalised to fractions of the frame (0-1 on each axis), the same choice
`manufacturing_intel` and Reality Connector's `phase7_zones` both made, so a zone drawn at one
camera resolution still lines up if the camera's resolution ever changes.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


class ZoneError(ValueError):
    """Bad input - the API turns these into 400s."""


# What kind of work happens in a zone (spec section 10's example zones), for Phase 5's activity
# engine to key its rules on. None means "just a zone" - it can still be used for tracking who's
# where, but no activity rule is tied to it. Free text elsewhere (the zone's own `name`) is for
# people to read; this is what code matches on, so a name like "Cutting Table (West)" still works.
ZONE_ACTIVITIES = ("marking", "cutting", "stitching", "piece_handling")


@dataclass
class Zone:
    id: int
    camera_id: str
    name: str
    points: list[list[float]]  # >= 3 points, each [x, y] as a fraction (0-1) of the frame
    activity: str | None = None  # one of ZONE_ACTIVITIES, or None for a zone with no specific activity
    created_at: float = field(default_factory=time.time)


def validate_activity(activity) -> str | None:
    if activity in (None, ""):
        return None
    a = str(activity).strip().lower()
    if a not in ZONE_ACTIVITIES:
        raise ZoneError(f"activity must be one of {', '.join(ZONE_ACTIVITIES)}, or left unset")
    return a


def validate_points(points) -> list[list[float]]:
    if not isinstance(points, list) or len(points) < 3:
        raise ZoneError("a zone needs at least 3 points")
    out = []
    for p in points:
        try:
            x, y = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError, KeyError):
            raise ZoneError("zone points must be [x, y] pairs") from None
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ZoneError("zone points must be between 0 and 1 (fractions of the frame)")
        out.append([round(x, 4), round(y, 4)])
    return out


class ZoneStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._zones: list[Zone] = []
        self._next_id = 1
        self._load()

    # ------------------------------------------------------------------- I/O
    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return  # a corrupt or unreadable file just means "start empty", not a crash
        self._zones = [Zone(**z) for z in raw.get("zones", [])]
        self._next_id = raw.get("next_id", (max((z.id for z in self._zones), default=0) + 1))

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"zones": [asdict(z) for z in self._zones], "next_id": self._next_id}, indent=1),
                       encoding="utf-8")
        tmp.replace(self.path)  # atomic on both Windows and POSIX for same-directory renames

    # ---------------------------------------------------------------------- CRUD
    def list_zones(self, camera_id: str | None = None) -> list[Zone]:
        with self._lock:
            zones = list(self._zones)
        return [z for z in zones if camera_id is None or z.camera_id == camera_id]

    def get_zone(self, zone_id: int) -> Zone | None:
        with self._lock:
            return next((z for z in self._zones if z.id == zone_id), None)

    def add_zone(self, camera_id: str, name: str, points, activity: str | None = None) -> Zone:
        name = (name or "").strip()
        if not name:
            raise ZoneError("a zone needs a name")
        if not camera_id or not str(camera_id).strip():
            raise ZoneError("a zone needs a camera_id")
        pts = validate_points(points)
        act = validate_activity(activity)
        with self._lock:
            if any(z.camera_id == camera_id and z.name.lower() == name.lower() for z in self._zones):
                raise ZoneError(f"a zone named '{name}' already exists on this camera")
            z = Zone(id=self._next_id, camera_id=camera_id, name=name, points=pts, activity=act)
            self._next_id += 1
            self._zones.append(z)
            self._save()
        return z

    def update_zone(self, zone_id: int, name: str | None = None, points=None, activity: str | None = None,
                    clear_activity: bool = False) -> Zone:
        """`activity=None` (the default) means "leave it as is" - pass `clear_activity=True` to
        explicitly unset it, since `None` is also the valid "no activity" value once set."""
        with self._lock:
            z = next((z for z in self._zones if z.id == zone_id), None)
            if z is None:
                raise ZoneError("no such zone")
            if name is not None:
                name = name.strip()
                if not name:
                    raise ZoneError("a zone needs a name")
                if any(o.id != zone_id and o.camera_id == z.camera_id and o.name.lower() == name.lower()
                      for o in self._zones):
                    raise ZoneError(f"a zone named '{name}' already exists on this camera")
                z.name = name
            if points is not None:
                z.points = validate_points(points)
            if clear_activity:
                z.activity = None
            elif activity is not None:
                z.activity = validate_activity(activity)
            self._save()
            return z

    def delete_zone(self, zone_id: int) -> None:
        with self._lock:
            before = len(self._zones)
            self._zones = [z for z in self._zones if z.id != zone_id]
            if len(self._zones) == before:
                raise ZoneError("no such zone")
            self._save()
