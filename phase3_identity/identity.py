"""Per-track identity state machine.

Implements the three-state model from the BRD (S14) and the alert-gating rules
from the acceptance doc (S5.5 / S5.6):

  UNVERIFIED   - the track has no quality-passed face; make NO identity claim
  RECOGNIZED   - >= confirm_frames quality-passed matches above threshold T
  UNKNOWN      - >= confirm_frames quality-passed non-matches

Flips between RECOGNIZED and UNKNOWN are rate-limited (flip_cooldown_s).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class IdentityState(str, Enum):
    UNVERIFIED = "UNVERIFIED"
    RECOGNIZED = "RECOGNIZED"
    UNKNOWN = "UNKNOWN"


@dataclass
class TrackIdentity:
    track_id: int
    state: IdentityState = IdentityState.UNVERIFIED
    employee_code: str | None = None
    employee_name: str | None = None
    score: float = 0.0
    best_score: float = 0.0
    quality_faces: int = 0
    _rec_streak: int = 0
    _unk_streak: int = 0
    _last_flip: float = 0.0
    _last_seen: float = field(default_factory=time.time)

    def touch(self) -> None:
        self._last_seen = time.time()


class IdentityResolver:
    def __init__(self, cfg, gallery, logger) -> None:
        self.cfg = cfg
        self.gallery = gallery
        self.log = logger
        self.tracks: dict[int, TrackIdentity] = {}
        self._frame_i = 0

    def begin_frame(self) -> None:
        self._frame_i += 1

    def should_recognize(self, track_id: int) -> bool:
        """Throttle recognition per track: always while UNVERIFIED, else every Nth frame."""
        ti = self.tracks.get(track_id)
        if ti is None or ti.state is IdentityState.UNVERIFIED:
            return True
        return (self._frame_i % self.cfg.recognize_every_n) == 0

    def update(self, track_id: int, quality_face) -> TrackIdentity:
        """quality_face: a DetectedFace that passed the quality gate, or None."""
        ti = self.tracks.setdefault(track_id, TrackIdentity(track_id))
        ti.touch()

        if quality_face is None:
            return ti  # hold current state; nothing new asserted

        ti.quality_faces += 1
        match = self.gallery.match(quality_face.embedding)
        score = match.score if match else 0.0
        ti.best_score = max(ti.best_score, score)

        now = time.time()
        if match and score >= self.cfg.recognition_threshold:
            ti._rec_streak += 1
            ti._unk_streak = 0
            if ti._rec_streak >= self.cfg.identity_confirm_frames:
                changed = ti.state is not IdentityState.RECOGNIZED or ti.employee_code != match.employee.code
                if not changed or now - ti._last_flip >= self.cfg.identity_flip_cooldown_s or ti.state is IdentityState.UNVERIFIED:
                    if changed:
                        ti._last_flip = now
                        self.log.info("track %d -> RECOGNIZED %s (%s) score=%.2f",
                                      track_id, match.employee.name, match.employee.code, score)
                    ti.state = IdentityState.RECOGNIZED
                    ti.employee_code = match.employee.code
                    ti.employee_name = match.employee.name
                    ti.score = score
        else:
            ti._unk_streak += 1
            ti._rec_streak = 0
            if ti._unk_streak >= self.cfg.identity_confirm_frames:
                if ti.state is not IdentityState.UNKNOWN and (
                    ti.state is IdentityState.UNVERIFIED
                    or now - ti._last_flip >= self.cfg.identity_flip_cooldown_s
                ):
                    ti._last_flip = now
                    self.log.info("track %d -> UNKNOWN (best match %.2f < %.2f)",
                                  track_id, score, self.cfg.recognition_threshold)
                    ti.state = IdentityState.UNKNOWN
                    ti.employee_code = None
                    ti.employee_name = None
                    ti.score = score
        return ti

    def prune(self, live_track_ids: set[int], max_age_s: float = 10.0) -> None:
        now = time.time()
        for tid in list(self.tracks):
            if tid not in live_track_ids and now - self.tracks[tid]._last_seen > max_age_s:
                del self.tracks[tid]

    def counts(self) -> dict[str, int]:
        c = {s.value: 0 for s in IdentityState}
        for ti in self.tracks.values():
            c[ti.state.value] += 1
        return c
