"""Footfall / entry-exit counting.

The critical design point: YOLO re-detects the same person in every frame,
so counting detections (or even counting "person inside a region" checks,
the way Phase 9's queue monitor does) would count one real person hundreds
of times. Instead this counts *line crossings*, using each track's ID to
recognise "this is the same physical person as last frame":

  1. Every frame, compute each live track's ground point (bottom-centre of
     its box - the same "where are they standing" convention Phase 7/9 use).
  2. Classify which side of a configured line that point is on. A small
     margin band around the line is "too close to call" and ignored.
  3. A side flip only becomes a *confirmed* crossing once it holds for
     `confirm_frames` consecutive frames. This second gate is required in
     practice, not just the margin: real YOLO boxes wobble a few pixels
     frame to frame even for someone standing still, and live testing
     against a real tracker showed that alone flipping a single frame past
     the margin is enough to register - producing dozens of rapid, ~0.1s
     apart in/out pairs for one stationary track, not real crossings.
     Requiring several consecutive frames on the new side filters that out,
     since genuine walking motion holds a side for many frames while jitter
     reverts within one or two.

Wired into `VisionPipeline.frame_hooks` by `phase5_dashboard/server.py`, so
it runs every frame (no throttling - unlike Phase 9's queue monitor, missing
a frame here could miss the exact crossing moment).
"""
from __future__ import annotations

import time

from phase4_events.store import EventStore
from phase7_zones.geometry import ground_point

from .config import FootfallConfig


def _signed_side(a: list, b: list, p: tuple[float, float]) -> float:
    """Perpendicular signed distance of point p from line a->b, in the same
    normalised units as the points. Sign says which side; magnitude says how
    far - used as the "confidently on a side" margin test."""
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    length = (dx * dx + dy * dy) ** 0.5 or 1e-9
    cross = dx * (py - ay) - dy * (px - ax)
    return cross / length


class FootfallCounter:
    def __init__(self, cfg: FootfallConfig, store: EventStore, logger) -> None:
        self.cfg = cfg
        self.store = store
        self.log = logger
        # (line_id, track_id) -> confirmed side ("pos" | "neg")
        self._side: dict[tuple[int, int], str] = {}
        # (line_id, track_id) -> (candidate side, consecutive frames seen so
        # far) - a side flip only replaces `_side` once this reaches
        # cfg.confirm_frames; any frame that isn't the candidate side resets it.
        self._pending: dict[tuple[int, int], tuple[str, int]] = {}
        self._last_crossing: dict[tuple[int, int], float] = {}
        self._last_seen: dict[tuple[int, int], float] = {}
        self._last_prune = 0.0
        self._lines_cache: list = []
        self._lines_last_refresh = 0.0
        self.crossings_logged = 0

    def _lines_for(self, camera_id: str) -> list:
        now = time.time()
        if now - self._lines_last_refresh >= 5.0:
            self._lines_last_refresh = now
            self._lines_cache = self.store.list_count_lines(enabled_only=True)
        return [ln for ln in self._lines_cache if ln.camera_id == camera_id]

    def observe(self, camera_id: str, positions: dict[int, tuple[int, int, int, int]],
                frame_w: int, frame_h: int) -> None:
        lines = self._lines_for(camera_id)
        if not lines:
            return
        now = time.time()

        for line in lines:
            a, b = line.points[0], line.points[1]
            for tid, bbox in positions.items():
                key = (line.id, tid)
                self._last_seen[key] = now

                pt = ground_point(bbox, frame_w, frame_h)
                dist = _signed_side(a, b, pt)
                if abs(dist) < self.cfg.margin:
                    self._pending.pop(key, None)   # too close to call - cancel any pending flip
                    continue

                side = "pos" if dist > 0 else "neg"
                confirmed = self._side.get(key)
                if confirmed is None:
                    self._side[key] = side         # first-ever confident sighting - nothing to cross yet
                    self._pending.pop(key, None)
                    continue
                if side == confirmed:
                    self._pending.pop(key, None)   # back on the known side - cancel any pending flip
                    continue

                # `side` differs from the confirmed side - only act once it's
                # held for `confirm_frames` in a row, not on the first frame.
                pend_side, pend_count = self._pending.get(key, (side, 0))
                pend_count = pend_count + 1 if pend_side == side else 1
                if pend_count < self.cfg.confirm_frames:
                    self._pending[key] = (side, pend_count)
                    continue

                # Hard backstop, independent of margin/confirm_frames tuning:
                # someone standing/swaying right at the line can still hold a
                # "wrong" side for confirm_frames in a row by chance every so
                # often - a real person can't walk back through a doorway
                # within recross_cooldown_s of just having walked through it,
                # so refuse to count (and don't advance the confirmed side)
                # until the cooldown since this track's last counted crossing
                # on this line has elapsed. Keep retrying each frame after that.
                if now - self._last_crossing.get(key, 0.0) < self.cfg.recross_cooldown_s:
                    self._pending[key] = (side, pend_count)
                    continue

                self._pending.pop(key, None)
                self._side[key] = side
                self._last_crossing[key] = now
                entered = (side == "pos") == bool(line.positive_is_in)
                direction = "in" if entered else "out"
                self.store.add_crossing(line_id=line.id, camera_id=camera_id,
                                         track_id=tid, direction=direction, ts=now)
                self.crossings_logged += 1
                self.log.info("footfall '%s': track %d -> %s", line.name, tid, direction)

        if now - self._last_prune > self.cfg.stale_after_s:
            self._last_prune = now
            stale = [k for k, t in self._last_seen.items() if now - t > self.cfg.stale_after_s]
            for k in stale:
                self._side.pop(k, None)
                self._pending.pop(k, None)
                self._last_seen.pop(k, None)
