"""Rule engine: match new events against user-configured rules and raise alerts.

A rule matches an event when every condition it specifies holds (AND); a blank
condition means "any". The BRD's example (S15):

    IF Person Detected AND Identity = Unknown AND Camera = Main Entrance
    THEN Create High Priority Alert

becomes a Rule row with identity_status="UNKNOWN", camera_id="Main Entrance",
priority="high". Time-of-day conditions (S20) are time_start/time_end, which
wrap midnight when end < start (e.g. 20:00-06:00).
"""
from __future__ import annotations

import time

from phase4_events.store import Event, EventStore, Rule

from .config import Phase6Config
from .notify import notify_alert

_DEFAULT_RULE = dict(
    name="Unknown person - any camera",
    identity_status="UNKNOWN",
    camera_id=None,
    time_start=None,
    time_end=None,
    days_of_week=None,
    priority="high",
    notify_email=False,
    cooldown_s=120,
)


class RuleEngine:
    def __init__(self, cfg: Phase6Config, store: EventStore, logger) -> None:
        self.cfg = cfg
        self.store = store
        self.log = logger
        self._cooldown: dict[tuple[int, int], float] = {}  # (rule_id, track_id) -> last fired
        self._seed_defaults()

    def _seed_defaults(self) -> None:
        if self.store.list_rules():
            return
        self.store.add_rule(**_DEFAULT_RULE)
        self.log.info(
            "Seeded default rule: '%s' (priority=%s). Manage rules via GET/POST /api/rules.",
            _DEFAULT_RULE["name"], _DEFAULT_RULE["priority"],
        )

    # ------------------------------------------------------------------ main
    def process_events(self, events: list[Event]) -> None:
        if not events:
            return
        rules = [r for r in self.store.list_rules(enabled_only=True)]
        if not rules:
            return
        now = time.time()
        for ev in events:
            for rule in rules:
                if not self._matches(rule, ev):
                    continue
                key = (rule.id, ev.track_id)
                if now - self._cooldown.get(key, 0.0) < rule.cooldown_s:
                    continue
                self._cooldown[key] = now
                self._raise_alert(rule, ev)

    def _raise_alert(self, rule: Rule, ev: Event) -> None:
        alert = self.store.create_alert(ev, rule)
        who = ev.employee_name or ev.identity_status
        self.log.warning(
            "ALERT #%d [%s] rule='%s' camera=%s track=%d identity=%s conf=%.2f",
            alert.id, alert.priority.upper(), rule.name, ev.camera_id, ev.track_id,
            who, ev.confidence or 0.0,
        )
        if rule.notify_email:
            notify_alert(self.cfg, alert, ev, self.log)

    # -------------------------------------------------------------- matching
    def _matches(self, rule: Rule, ev: Event) -> bool:
        if rule.identity_status and rule.identity_status != ev.identity_status:
            return False
        if rule.camera_id and rule.camera_id != ev.camera_id:
            return False
        if not self._time_matches(rule, ev.ts):
            return False
        return True

    @staticmethod
    def _time_matches(rule: Rule, ts: float) -> bool:
        t = time.localtime(ts)
        if rule.days_of_week:
            allowed = {int(d) for d in rule.days_of_week.split(",") if d.strip() != ""}
            if t.tm_wday not in allowed:  # Monday=0 .. Sunday=6, same convention we store
                return False
        if not rule.time_start or not rule.time_end:
            return True
        try:
            sh, sm = (int(x) for x in rule.time_start.split(":"))
            eh, em = (int(x) for x in rule.time_end.split(":"))
        except ValueError:
            return True  # malformed window - fail open rather than silently never firing
        now_min = t.tm_hour * 60 + t.tm_min
        start, end = sh * 60 + sm, eh * 60 + em
        if start <= end:
            return start <= now_min < end
        return now_min >= start or now_min < end  # wraps midnight
