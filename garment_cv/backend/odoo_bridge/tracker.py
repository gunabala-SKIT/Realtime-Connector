"""Turns the camera's stream of "what activity is happening" into work-order decisions: start
Marking, hand off to Cutting, finish Cutting.

Pure logic - no Odoo, camera or database code - so every rule below is testable with hand-built
observation sequences (`tests/test_odoo_tracker.py`), the same discipline `activities/engine.py`
follows.

Why a separate state machine rather than reacting to each completed activity: the activity engine
works per tracked person and a track can flicker or be re-identified mid-job. A work order must not
start, finish or restart because of that. This tracker only looks at "is activity X confirmed
anywhere in view right now" and accumulates its own evidence over time.

The finishing rule is the heart of it. The user's instruction: picking a scissor up from the fabric
must not finish an operation. So nothing finishes on a single event; each transition needs
sustained evidence:

* START step k    - its activity has been continuously confirmed for `start_hold_s`.
* FINISH step i / START step i+1 (hand-off)
                  - the NEXT step's activity has been continuously confirmed for `handoff_hold_s`.
                    Putting the pen down, or touching scissors without cutting, confirms nothing.
* FINISH the last step
                  - at least `min_active_s` of its activity was seen, AND then `quiet_s` of healthy
                    camera time passed without it. Quiet time only accrues on frames that actually
                    arrived (a camera outage pauses the clock instead of looking like "quiet"), and
                    seeing the activity again cancels the countdown - the same work order continues.

Decisions are applied optimistically (the tracker moves on as soon as it emits one) and corrected by
`sync_states` when the Odoo side disagrees - Odoo is the source of truth, see `bridge.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

PENDING, ACTIVE, FINISHED = "pending", "active", "finished"


@dataclass(frozen=True)
class FlowConfig:
    start_hold_s: float = 2.0        # activity must stay confirmed this long before a step starts
    handoff_hold_s: float = 3.0      # next step's activity must stay confirmed this long to hand off
    min_active_s: float = 10.0       # last step: minimum cumulative activity before it may finish
    quiet_s: float = 15.0            # last step: healthy no-activity time before it finishes - the worker
                                     # idle, walking away or otherwise not cutting for this long ends the job
    hold_grace_s: float = 3.0        # a hold survives the activity dropping out for this long
    max_gap_s: float = 5.0           # observations further apart than this are a camera outage:
                                     # the clock pauses rather than counting it as activity or quiet


@dataclass(frozen=True)
class Step:
    name: str                        # the Odoo operation name, e.g. "Marking"
    activity: str                    # the engine activity that means it is happening, e.g. "MARKING"


@dataclass(frozen=True)
class Decision:
    kind: str                        # "START" or "FINISH"
    step: int
    ts: float
    reason: str
    evidence: dict = field(default_factory=dict)


class OperationTracker:
    def __init__(self, steps: list[Step], cfg: FlowConfig | None = None) -> None:
        if not steps:
            raise ValueError("a flow needs at least one step")
        self.steps = list(steps)
        self.cfg = cfg or FlowConfig()
        self.states = [PENDING] * len(steps)
        self.held = False                       # operator pressed "hold": never auto-finish
        self._since: dict[str, float] = {}      # activity -> when its current streak began
        self._last_seen: dict[str, float] = {}
        self._last_ts: float | None = None
        self._active_total = 0.0                # cumulative seconds of the last step's activity
        self._quiet = 0.0
        self.unexpected: str | None = None      # last "activity out of order" note, for the dashboard

    # ------------------------------------------------------------------ evidence bookkeeping
    def _held_for(self, activity: str, ts: float) -> float:
        """How long the activity has been confirmed, counted only while it is confirmed *right now*
        (a streak bridging a short dropout still counts, but a decision never fires on the strength
        of an activity that has just stopped)."""
        since = self._since.get(activity)
        if since is None or self._last_seen.get(activity) != ts:
            return 0.0
        return ts - since

    def _track_streaks(self, activities: frozenset[str], ts: float) -> None:
        for a in {s.activity for s in self.steps}:
            if a in activities:
                self._since.setdefault(a, ts)
                self._last_seen[a] = ts
            elif ts - self._last_seen.get(a, float("-inf")) > self.cfg.hold_grace_s:
                self._since.pop(a, None)

    def _current(self) -> int | None:
        return next((i for i, s in enumerate(self.states) if s == ACTIVE), None)

    def _next_pending(self) -> int | None:
        for i, s in enumerate(self.states):
            if s == PENDING:
                return i
            if s == ACTIVE:
                return None
        return None

    # ------------------------------------------------------------------------- main entry
    def observe(self, ts: float, activities: frozenset[str] | set[str], healthy: bool = True) -> list[Decision]:
        activities = frozenset(activities)
        dt = 0.0 if self._last_ts is None else ts - self._last_ts
        if dt < 0:                                  # clock stepped backwards: ignore this sample
            return []
        self._last_ts = ts
        counts = healthy and 0.0 < dt <= self.cfg.max_gap_s
        if not healthy or dt > self.cfg.max_gap_s:
            # A gap/outage proves nothing either way: forget streaks (they can't be continuous
            # across it) and let the quiet clock pause. Cumulative activity time is kept.
            self._since.clear()
        if healthy:
            self._track_streaks(activities, ts)

        decisions: list[Decision] = []
        cur = self._current()

        if cur is None:
            k = self._next_pending()
            if k is not None and healthy:
                held = self._held_for(self.steps[k].activity, ts)
                if held >= self.cfg.start_hold_s:
                    decisions.append(self._start(k, ts, f"{self.steps[k].activity} confirmed for {held:.1f}s",
                                                 {"held_s": round(held, 1)}))
            self._note_unexpected(activities, expected={self.steps[k].activity} if k is not None else set())
            return decisions

        last = cur == len(self.steps) - 1
        if not last:
            nxt = self.steps[cur + 1]
            held = self._held_for(nxt.activity, ts) if healthy else 0.0
            if held >= self.cfg.handoff_hold_s:
                decisions.append(self._finish(cur, ts, f"{nxt.activity} confirmed for {held:.1f}s - hand-off",
                                              {"held_s": round(held, 1)}))
                decisions.append(self._start(cur + 1, ts, f"hand-off from {self.steps[cur].name}", {}))
            self._note_unexpected(activities, expected={self.steps[cur].activity, nxt.activity})
            return decisions

        # the last step: finishing needs sustained activity followed by sustained quiet
        mine = self.steps[cur].activity
        if mine in activities and healthy:
            if counts:
                self._active_total += dt
            self._quiet = 0.0
        elif counts:
            self._quiet += dt
        if (not self.held and self._active_total >= self.cfg.min_active_s and self._quiet >= self.cfg.quiet_s):
            decisions.append(self._finish(cur, ts, f"{self._active_total:.0f}s of {mine} then {self._quiet:.0f}s quiet",
                                          {"active_s": round(self._active_total, 1), "quiet_s": round(self._quiet, 1)}))
        self._note_unexpected(activities, expected={mine})
        return decisions

    def _note_unexpected(self, activities: frozenset[str], expected: set[str]) -> None:
        known = {s.activity for s in self.steps}
        stray = sorted((activities & known) - expected)
        self.unexpected = f"{', '.join(stray)} seen out of order - no change made" if stray else None

    def _start(self, k: int, ts: float, reason: str, evidence: dict) -> Decision:
        self.states[k] = ACTIVE
        self._active_total = 0.0
        self._quiet = 0.0
        return Decision("START", k, ts, reason, evidence)

    def _finish(self, k: int, ts: float, reason: str, evidence: dict) -> Decision:
        self.states[k] = FINISHED
        self._quiet = 0.0
        return Decision("FINISH", k, ts, reason, evidence)

    # ------------------------------------------------------------------ outside corrections
    def sync_states(self, states: list[str]) -> bool:
        """Adopt the states Odoo reports (the source of truth). Returns True if anything changed.
        Evidence for a step that just became ACTIVE starts from zero."""
        if len(states) != len(self.states):
            raise ValueError("state list does not match the flow's steps")
        if states == self.states:
            return False
        for i, (old, new) in enumerate(zip(self.states, states)):
            if new == ACTIVE and old != ACTIVE:
                self._active_total = 0.0
                self._quiet = 0.0
        self.states = list(states)
        return True

    def reset(self) -> None:
        self.states = [PENDING] * len(self.steps)
        self._since.clear()
        self._last_seen.clear()
        self._last_ts = None
        self._active_total = self._quiet = 0.0
        self.unexpected = None

    def finishing_in_s(self) -> float | None:
        """Seconds until the last step would auto-finish if nothing else happens, or None."""
        cur = self._current()
        if (cur is None or cur != len(self.steps) - 1 or self.held
                or self._active_total < self.cfg.min_active_s or self._quiet <= 0):
            return None
        return max(0.0, self.cfg.quiet_s - self._quiet)

    def status(self) -> dict:
        cur = self._current()
        return {
            "steps": [{"name": s.name, "activity": s.activity, "state": st}
                      for s, st in zip(self.steps, self.states)],
            "current": cur, "held": self.held,
            "active_s": round(self._active_total, 1), "quiet_s": round(self._quiet, 1),
            "finishing_in_s": self.finishing_in_s(), "unexpected": self.unexpected,
            # how long each step's activity has currently been continuously confirmed (live diagnostics)
            "held_s": {s.activity: round(self._held_for(s.activity, self._last_ts), 1) if self._last_ts else 0.0
                       for s in self.steps},
            "start_hold_s": self.cfg.start_hold_s, "handoff_hold_s": self.cfg.handoff_hold_s,
        }

    def snapshot(self) -> dict:
        return {"states": list(self.states), "active_total": self._active_total, "held": self.held}

    def restore(self, snap: dict) -> None:
        if len(snap.get("states", [])) == len(self.states):
            self.states = list(snap["states"])
            self._active_total = float(snap.get("active_total", 0.0))
            self.held = bool(snap.get("held", False))
