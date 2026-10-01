"""Production events (spec section 12): a completed activity, persisted. This is Phase 6's actual
deliverable - "convert activities into Start/End/Duration/Worker/Camera/Zone/Confidence and store
them in the database" - and nothing more. It doesn't duplicate `ActivityEngine`'s own in-memory
`recent()` history (Phase 5); that stays as the fast, no-DB-round-trip source for "what just
happened" on the live view. This store is the durable record analytics and the dashboard's
timeline/history views (Phase 7) read from.

IDLE completions are stored here too, not just the four production stages - spec section 17 asks
for "Idle Time" to be calculated, and a complete record of IDLE spans makes that a straight sum
rather than something inferred from the gaps between other events.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from ..database.db import Database


@dataclass(frozen=True)
class ProductionEvent:
    id: int
    camera_id: str
    track_id: int
    worker: str
    stage: str
    zone: str | None
    start_ts: float
    end_ts: float
    duration_s: float
    confidence: float
    created_at: float

    def to_dict(self) -> dict:
        return {
            "id": self.id, "camera_id": self.camera_id, "track_id": self.track_id,
            "worker": self.worker, "stage": self.stage, "zone": self.zone,
            "start_ts": self.start_ts, "end_ts": self.end_ts,
            "duration_s": round(self.duration_s, 1), "confidence": self.confidence,
            "created_at": self.created_at,
        }


def _row_to_event(row) -> ProductionEvent:
    return ProductionEvent(id=row["id"], camera_id=row["camera_id"], track_id=row["track_id"],
                           worker=row["worker"], stage=row["stage"], zone=row["zone"],
                           start_ts=row["start_ts"], end_ts=row["end_ts"],
                           duration_s=row["duration_s"], confidence=row["confidence"],
                           created_at=row["created_at"])


class EventStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def record(self, *, camera_id: str, track_id: int, worker: str, stage: str,
              zone: str | None, start_ts: float, end_ts: float, duration_s: float,
              confidence: float) -> ProductionEvent:
        created_at = time.time()
        cur = self.db.execute(
            "INSERT INTO production_events "
            "(camera_id, track_id, worker, stage, zone, start_ts, end_ts, duration_s, confidence, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (camera_id, track_id, worker, stage, zone, start_ts, end_ts, duration_s, confidence, created_at),
        )
        return ProductionEvent(id=int(cur.lastrowid), camera_id=camera_id, track_id=track_id,
                               worker=worker, stage=stage, zone=zone, start_ts=start_ts,
                               end_ts=end_ts, duration_s=duration_s, confidence=confidence,
                               created_at=created_at)

    def list(self, *, camera_id: str | None = None, stage: str | None = None,
            worker: str | None = None, since_ts: float | None = None,
            limit: int = 100) -> list[ProductionEvent]:
        where, params = [], []
        if camera_id is not None:
            where.append("camera_id = ?"); params.append(camera_id)
        if stage is not None:
            where.append("stage = ?"); params.append(stage)
        if worker is not None:
            where.append("worker = ?"); params.append(worker)
        if since_ts is not None:
            where.append("start_ts >= ?"); params.append(since_ts)
        sql = "SELECT * FROM production_events"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY start_ts DESC LIMIT ?"
        params.append(max(1, min(limit, 1000)))
        return [_row_to_event(r) for r in self.db.query(sql, tuple(params))]

    def stage_summary(self, *, since_ts: float | None = None) -> dict[str, dict]:
        """Per-stage count/average/total duration, for Phase 7's "Average X Time" and stage
        performance tables (spec section 17) - one grouped query, not one per stage."""
        sql = "SELECT stage, COUNT(*) AS n, AVG(duration_s) AS avg_s, SUM(duration_s) AS total_s FROM production_events"
        params: tuple = ()
        if since_ts is not None:
            sql += " WHERE start_ts >= ?"
            params = (since_ts,)
        sql += " GROUP BY stage"
        return {r["stage"]: {"count": r["n"], "avg_s": round(r["avg_s"], 1), "total_s": round(r["total_s"], 1)}
               for r in self.db.query(sql, params)}

    def worker_active_seconds(self, *, since_ts: float | None = None) -> dict[str, float]:
        """Total non-IDLE duration per worker, for idle-time calculation (spec section 17:
        "Idle Time = Total Shift Time - Active Production Time")."""
        sql = "SELECT worker, SUM(duration_s) AS total_s FROM production_events WHERE stage != 'IDLE'"
        params: tuple = ()
        if since_ts is not None:
            sql += " AND start_ts >= ?"
            params = (since_ts,)
        sql += " GROUP BY worker"
        return {r["worker"]: round(r["total_s"], 1) for r in self.db.query(sql, params)}

    def worker_idle_seconds(self, *, since_ts: float | None = None) -> dict[str, float]:
        sql = "SELECT worker, SUM(duration_s) AS total_s FROM production_events WHERE stage = 'IDLE'"
        params: tuple = ()
        if since_ts is not None:
            sql += " AND start_ts >= ?"
            params = (since_ts,)
        sql += " GROUP BY worker"
        return {r["worker"]: round(r["total_s"], 1) for r in self.db.query(sql, params)}
