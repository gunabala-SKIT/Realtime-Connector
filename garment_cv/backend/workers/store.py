"""Manual worker assignment (spec section 13): "For MVP, allow manual worker assignment if
automatic identification is not yet reliable. Do not make face recognition mandatory for the
first version." No face recognition, badge or RFID integration exists in this product - a
tracked person is only ever a `track_id` (Phase 3) until an administrator labels it here.

Keyed by `track_id` alone, not by any persistent cross-session identity: tracking IDs are
explicitly temporary (Phase 3's own docstring, spec section 9), so an assignment only means "while
this track_id is active, call it this name" - it's meant to be re-applied each time a worker is
recognized at the start of their shift, not a permanent employee roster. A real roster (reusable
across sessions, tied to a durable identity) is future work once a real identification method
exists, consistent with the spec's own phasing.
"""
from __future__ import annotations

import time

from ..database.db import Database


class WorkerStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def assign(self, track_id: int, name: str) -> dict:
        name = name.strip()
        if not name:
            raise ValueError("worker name must not be empty")
        assigned_at = time.time()
        self.db.execute(
            "INSERT INTO workers (track_id, name, assigned_at) VALUES (?, ?, ?) "
            "ON CONFLICT(track_id) DO UPDATE SET name = excluded.name, assigned_at = excluded.assigned_at",
            (track_id, name, assigned_at),
        )
        return {"track_id": track_id, "name": name, "assigned_at": assigned_at}

    def unassign(self, track_id: int) -> None:
        self.db.execute("DELETE FROM workers WHERE track_id = ?", (track_id,))

    def list(self) -> list[dict]:
        rows = self.db.query("SELECT * FROM workers ORDER BY assigned_at DESC")
        return [{"track_id": r["track_id"], "name": r["name"], "assigned_at": r["assigned_at"]} for r in rows]

    def name_for(self, track_id: int | None) -> str:
        """The assigned name for a track, or the `track-{id}` fallback every event is guaranteed
        to have a readable `worker` value even when nobody has assigned one."""
        if track_id is None:
            return "unknown"
        rows = self.db.query("SELECT name FROM workers WHERE track_id = ?", (track_id,))
        return rows[0]["name"] if rows else f"track-{track_id}"
