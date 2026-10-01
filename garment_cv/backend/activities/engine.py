"""The activity engine (spec section 11, section 22): turns a stream of already-detected,
already-tracked, already-zoned objects into MARKING / CUTTING / STITCHING / PIECE_HANDLING / IDLE
activities, using object + zone + movement + duration - never a single frame's object presence
alone (spec section 3: "worker holding scissors ≠ cutting").

No camera, model or zone-storage code here - `update()` takes plain per-frame data (a list of
detection-like objects, a timestamp, the frame size) and returns what's currently going on. That
keeps it testable with hand-built scenarios (`tests/test_activity_engine.py`), the same
discipline `zones/` already follows.
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Protocol

from .config import ActivityConfig
from .rules import IDLE, REACHABLE_NOW, RULES


class _DetectionLike(Protocol):
    cls: str
    box: tuple[float, float, float, float]
    track_id: int | None
    zone: str | None
    zone_activity: str | None
    conf: float


@dataclass(frozen=True)
class CompletedActivity:
    camera_id: str
    track_id: int
    activity: str
    zone: str | None
    start_ts: float
    end_ts: float
    duration_s: float
    confidence: float


@dataclass
class _TrackState:
    rule_since: dict[str, float] = field(default_factory=dict)       # rule name -> when its raw condition first held
    rule_last_ok: dict[str, float] = field(default_factory=dict)     # rule name -> last tick it held (for grace)
    rule_positions: dict[str, deque] = field(default_factory=dict)   # rule name -> deque[(ts, cx, cy)] of the
                                                                     # attributed object, for movement rules
    rule_confidences: dict[str, list[float]] = field(default_factory=dict)
    active: str | None = None       # the rule confirmed and ongoing right now (or "IDLE"), if any
    active_since: float = 0.0
    camera_id: str = ""
    zone: str | None = None
    last_seen: float = 0.0


def _center(box: tuple[float, float, float, float]) -> tuple[float, float]:
    return (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0


def _attribute(person_box, candidates: list[_DetectionLike], margin: float) -> _DetectionLike | None:
    """The candidate object most likely to belong to this person: its centre must fall inside the
    person's own box expanded by `margin` on every side (a hand holding a tool reaches past the
    body outline), and among those, the nearest one to the person's own centre wins."""
    x1, y1, x2, y2 = person_box
    w, h = max(x2 - x1, 1.0), max(y2 - y1, 1.0)
    rx1, ry1, rx2, ry2 = x1 - margin * w, y1 - margin * h, x2 + margin * w, y2 + margin * h
    pcx, pcy = _center(person_box)
    best, best_d = None, float("inf")
    for c in candidates:
        cx, cy = _center(c.box)
        if not (rx1 <= cx <= rx2 and ry1 <= cy <= ry2):
            continue
        d = (cx - pcx) ** 2 + (cy - pcy) ** 2
        if d < best_d:
            best, best_d = c, d
    return best


class ActivityEngine:
    def __init__(self, cfg: ActivityConfig, on_complete: Callable[[CompletedActivity], None] | None = None) -> None:
        self.cfg = cfg
        self._tracks: dict[int, _TrackState] = {}
        self._history: deque[CompletedActivity] = deque(maxlen=max(1, cfg.max_history))
        self._lock = threading.Lock()
        # Phase 6's seam: called with each activity as soon as it completes. `update()` invokes
        # it only after releasing `self._lock` (see below), so a slow listener (e.g. a database
        # write) blocks neither a concurrent `current()`/`recent()`/`status()` call from the API
        # nor this method's own bookkeeping - only the next call to `update()` itself, which is
        # already how every other frame hook in this codebase behaves (see `CameraRunner`'s
        # docstring on `frame_hooks`). `ActivityEngine` never imports `events/` or `database/`
        # itself - the same decoupling already used for `_ZoneLookup`/`_ActivityLookup` above.
        self._on_complete = on_complete

    def _min_duration(self, rule_name: str) -> float:
        return {"CUTTING": self.cfg.cutting_min_s, "MARKING": self.cfg.marking_min_s,
               "STITCHING": self.cfg.stitching_min_s, "PIECE_HANDLING": self.cfg.piece_handling_min_s,
               IDLE: self.cfg.idle_min_s}[rule_name]

    # ------------------------------------------------------------------------ update
    def update(self, camera_id: str, detections: list[_DetectionLike], ts: float,
              frame_w: int, frame_h: int) -> dict[int, str | None]:
        """One detection cycle's worth of (already zone/track enriched) detections. Returns
        {track_id: activity currently confirmed for them, or None} for every tracked person seen
        this call - used to label the live annotated view immediately, without a second lookup."""
        if not self.cfg.enabled:
            return {}
        diag = max((frame_w ** 2 + frame_h ** 2) ** 0.5, 1.0)
        people = [d for d in detections if d.cls == "person" and d.track_id is not None]
        others = [d for d in detections if d.cls != "person"]
        current: dict[int, str | None] = {}
        completed: list[CompletedActivity] = []
        with self._lock:
            for p in people:
                st = self._tracks.setdefault(p.track_id, _TrackState())
                st.last_seen, st.camera_id, st.zone = ts, camera_id, p.zone
                new_active = self._evaluate_person(st, p, others, ts, diag)
                if new_active != st.active:
                    if st.active is not None:
                        completed.append(self._complete(camera_id, p.track_id, st, ts))
                    if new_active is not None:
                        st.active_since = st.rule_since.get(new_active, ts)
                    st.active = new_active
                current[p.track_id] = st.active
            completed.extend(self._prune(ts))
        if self._on_complete is not None:
            for c in completed:
                self._on_complete(c)
        return current

    def _evaluate_person(self, st: _TrackState, p: _DetectionLike, others: list[_DetectionLike],
                         ts: float, diag: float) -> str | None:
        any_raw_ok = False
        matched = None
        for rule in RULES:
            ok, obj = False, None
            if p.zone_activity == rule.zone_activity:
                candidates = [d for d in others if d.cls in rule.required_classes]
                obj = _attribute(p.box, candidates, self.cfg.attribution_margin)
                if obj is not None:
                    if rule.requires_movement:
                        ok = self._moved_enough(st, rule.name, obj, ts, diag)
                    else:
                        ok = True
            any_raw_ok = any_raw_ok or ok
            # Recorded as soon as the object is attributed to this person, not gated on `ok` -
            # a movement rule's first tick or two is always `ok=False` (movement needs 2+ samples
            # to measure at all), but the object was still genuinely there with that confidence;
            # dropping those early readings would understate a completed activity's average.
            if obj is not None:
                st.rule_confidences.setdefault(rule.name, []).append(obj.conf)
            confirmed = self._advance(st, rule.name, ok, ts, self._min_duration(rule.name))
            if confirmed and matched is None:
                matched = rule.name
        # IDLE is just another rule, always "ok" exactly when nothing else's raw condition is -
        # `_advance` already gives it the same grace-period tolerance as every other rule, so a
        # single stray detection doesn't zero out an otherwise-solid idle streak either.
        idle_confirmed = self._advance(st, IDLE, not any_raw_ok, ts, self.cfg.idle_min_s)
        if matched is None and idle_confirmed:
            matched = IDLE
        return matched

    def _moved_enough(self, st: _TrackState, rule_name: str, obj: _DetectionLike, ts: float, diag: float) -> bool:
        buf = st.rule_positions.setdefault(rule_name, deque())
        cx, cy = _center(obj.box)
        buf.append((ts, cx, cy))
        while buf and ts - buf[0][0] > self.cfg.movement_window_s:
            buf.popleft()
        if len(buf) < 2:
            return False
        x0, y0 = buf[0][1], buf[0][2]
        spread = max(((x - x0) ** 2 + (y - y0) ** 2) ** 0.5 for _, x, y in buf)
        return (spread / diag) >= self.cfg.movement_min_fraction

    def _advance(self, st: _TrackState, rule_name: str, ok: bool, ts: float, min_duration: float) -> bool:
        """Shared streak/grace/confirm bookkeeping for one rule on one track (the same shape as
        the confirm-then-cooldown pattern already used for PPE compliance elsewhere in this
        codebase): a rule confirms once its raw condition has held continuously for
        `min_duration`, and survives a gap shorter than `grace_s` (one missed detection frame -
        an occlusion, a hand blocking the view - must not restart the timer from zero)."""
        if ok:
            since = st.rule_since.get(rule_name)
            if since is None:
                st.rule_since[rule_name] = ts
                since = ts
            st.rule_last_ok[rule_name] = ts
            return ts - since >= min_duration
        last_ok = st.rule_last_ok.get(rule_name)
        if last_ok is None or ts - last_ok > self.cfg.grace_s:
            # Only the confirmation streak resets here - NOT `rule_positions`. That buffer is a
            # sliding time window `_moved_enough` prunes on its own; a rule's very first tick is
            # always "not ok yet" (it takes 2 samples to detect movement at all), so wiping
            # positions here too would erase a fresh streak's only sample before a second one
            # ever arrives to compare against it - movement could then never be detected. A real
            # gap still self-heals: samples older than `movement_window_s` age out naturally the
            # next time `_moved_enough` runs.
            st.rule_since.pop(rule_name, None)
            st.rule_last_ok.pop(rule_name, None)
            # Confidences are trickier: `last_ok is None` also covers a rule's very first tick
            # ever (nothing to reset - it just hasn't been `ok` yet, e.g. a movement rule needs a
            # 2nd sample before it can even measure movement), and the confidence for THIS tick's
            # attributed object was already appended above in `_evaluate_person` before `_advance`
            # was called - clearing here would erase it before it ever got to count. So only pop
            # on a genuine gap (`last_ok` was set, meaning the rule really had been holding, and
            # has now been stale past the grace period) - and even then, only if this rule isn't
            # the track's currently-active activity, since that case is the very tick `update()`
            # is about to call `_complete()` for, which needs `rule_confidences` intact to compute
            # the finished activity's average (its own `_reset()` clears it right after).
            if last_ok is not None and rule_name != st.active:
                st.rule_confidences.pop(rule_name, None)
        return False

    def _reset(self, st: _TrackState, rule_name: str) -> None:
        """Full cleanup once a rule's activity has actually completed (`_complete`) - unlike
        `_advance`'s gap handling, this also clears `rule_positions`, so a future new episode of
        the same rule starts its movement measurement from scratch rather than comparing against
        a stale, long-finished one."""
        st.rule_since.pop(rule_name, None)
        st.rule_last_ok.pop(rule_name, None)
        st.rule_positions.pop(rule_name, None)
        st.rule_confidences.pop(rule_name, None)

    def _complete(self, camera_id: str, track_id: int, st: _TrackState, ts: float) -> CompletedActivity:
        confs = st.rule_confidences.get(st.active, [])
        confidence = (sum(confs) / len(confs)) if confs else 1.0  # IDLE has no object confidence to average
        completed = CompletedActivity(camera_id=camera_id, track_id=track_id, activity=st.active,
                                      zone=st.zone, start_ts=st.active_since, end_ts=ts,
                                      duration_s=ts - st.active_since, confidence=round(confidence, 3))
        self._history.appendleft(completed)
        self._reset(st, st.active)
        return completed

    def _prune(self, ts: float, stale_s: float = 30.0) -> list[CompletedActivity]:
        """A track that's vanished (left the frame, tracking lost it) longer than `stale_s`
        finalises whatever was active for it and stops being remembered - a reused track id later
        must not inherit a stranger's in-progress activity."""
        gone = [tid for tid, st in self._tracks.items() if ts - st.last_seen > stale_s]
        completed = []
        for tid in gone:
            st = self._tracks.pop(tid)
            if st.active is not None:
                completed.append(self._complete(st.camera_id, tid, st, st.last_seen))
        return completed

    # -------------------------------------------------------------------- introspection
    def recent(self, limit: int = 50) -> list[dict]:
        with self._lock:
            items = list(self._history)[:limit]
        return [{"camera_id": a.camera_id, "track_id": a.track_id, "activity": a.activity, "zone": a.zone,
                "start_ts": a.start_ts, "end_ts": a.end_ts, "duration_s": round(a.duration_s, 1),
                "confidence": a.confidence} for a in items]

    def current(self) -> dict[int, dict]:
        """What every currently-tracked person is doing right now, for the live view - separate
        from `update()`'s return value so the API can ask independently of the capture thread."""
        with self._lock:
            return {tid: {"activity": st.active, "zone": st.zone,
                         "since": st.active_since if st.active else None}
                   for tid, st in self._tracks.items()}

    def status(self) -> dict:
        return {"enabled": self.cfg.enabled, "rules": [r.name for r in RULES], "reachable_now": sorted(REACHABLE_NOW),
               "dormant": sorted(r.name for r in RULES if r.name not in REACHABLE_NOW),
               "tracked": len(self._tracks), "history_count": len(self._history)}
