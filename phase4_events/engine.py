"""Event engine: turn per-track identity states into deduplicated events.

Rule (MVP Acceptance Criteria S7): **exactly one event per
(camera_id, track_id, identity_status)**. A track that goes
UNVERIFIED -> RECOGNIZED emits one RECOGNIZED event; staying RECOGNIZED emits
nothing more; RECOGNIZED -> UNKNOWN emits one UNKNOWN event. When a track is
pruned (gone for `dedup_track_ttl_s`), its dedup memory is cleared, so if
BoT-SORT later reuses that id for a new person it counts as a fresh track.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path

import cv2

from phase3_identity.identity import IdentityState
from .store import Event, EventStore

_TYPE = {
    IdentityState.RECOGNIZED: "identity_recognized",
    IdentityState.UNKNOWN: "identity_unknown",
    IdentityState.UNVERIFIED: "identity_unverified",
}


class EventEngine:
    def __init__(self, cfg, store: EventStore, logger) -> None:
        self.cfg = cfg
        self.store = store
        self.log = logger
        self.snapshot_dir = Path(cfg.snapshot_dir)
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self._emitted: dict[int, dict[str, float]] = {}   # track_id -> {status: last_seen}
        self.count = 0

    def observe(self, track_identities: dict, live_ids: set[int], frame) -> list[Event]:
        """Call once per processed frame with the current TrackIdentity objects."""
        now = time.time()
        new_events: list[Event] = []

        for tid, ti in track_identities.items():
            if tid not in live_ids:
                continue
            status = ti.state.value
            if status == IdentityState.UNVERIFIED.value and not self.cfg.emit_unverified:
                continue

            seen = self._emitted.setdefault(tid, {})
            seen["_alive"] = now
            if status in seen:
                seen[status] = now
                continue
            seen[status] = now
            new_events.append(self._emit(tid, ti, frame, now))

        self._prune(live_ids, now)
        return new_events

    def _emit(self, track_id: int, ti, frame, ts: float) -> Event:
        uid = uuid.uuid4().hex
        snap_rel = None
        try:
            snap_rel = self._save_snapshot(uid, ti, frame, ts)
        except Exception as e:  # noqa: BLE001 - snapshot is best-effort evidence
            self.log.warning("snapshot for %s failed: %s", uid, e)

        ev = Event(
            id=None,
            event_uid=uid,
            ts=ts,
            camera_id=self.cfg.camera_id,
            track_id=track_id,
            event_type=_TYPE.get(ti.state, "identity_unverified"),
            identity_status=ti.state.value,
            employee_code=ti.employee_code,
            employee_name=ti.employee_name,
            confidence=round(ti.score, 4) if ti.state is IdentityState.RECOGNIZED else round(ti.best_score, 4),
            zone=None,
            snapshot=snap_rel,
            alert_status="none",
            created_at=time.time(),
        )
        ev = self.store.insert(ev)
        self.count += 1
        who = ev.employee_name or ev.identity_status
        self.log.info("event #%d  %s  cam=%s track=%d  %s  conf=%.2f  -> %s",
                      ev.id, ev.event_type, ev.camera_id, ev.track_id, who,
                      ev.confidence or 0.0, ev.snapshot or "(no snapshot)")
        return ev

    def _save_snapshot(self, uid: str, ti, frame, ts: float) -> str:
        img = frame.copy()
        h, w = img.shape[:2]
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
        label = f"{ti.employee_name or ti.state.value}  track {ti.track_id}  {stamp}  cam {self.cfg.camera_id}"
        cv2.rectangle(img, (0, h - 26), (w, h), (0, 0, 0), -1)
        cv2.putText(img, label, (8, h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (255, 255, 255), 1, cv2.LINE_AA)
        day_dir = self.snapshot_dir / time.strftime("%Y-%m-%d", time.localtime(ts))
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / f"{uid}.jpg"
        cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return str(path.relative_to(self.snapshot_dir)).replace("\\", "/")

    def _prune(self, live_ids: set[int], now: float) -> None:
        ttl = self.cfg.dedup_track_ttl_s
        for tid in list(self._emitted):
            if tid in live_ids:
                continue
            if now - self._emitted[tid].get("_alive", 0) > ttl:
                del self._emitted[tid]
