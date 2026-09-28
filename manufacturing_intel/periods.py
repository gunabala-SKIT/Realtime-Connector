"""Time windows: today / yesterday / shifts / weeks / months / custom, in the factory's local time.

Every window is a half-open interval [start, end) of epoch seconds. A window that
hasn't finished yet (`end > now`) is "live"; comparing it with the previous period
is done *like for like* - yesterday only up to the same time of day - because
"380 today vs 450 yesterday" at 2 pm compares half a day with a whole one.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

PERIODS = ("last_hour", "last_24h", "today", "yesterday", "current_shift", "previous_shift",
           "this_week", "last_week", "this_month", "last_month", "last_7d", "last_30d", "custom")
MAX_SPAN_DAYS = 400


class PeriodError(ValueError):
    pass


@dataclass(frozen=True)
class Window:
    start: float
    end: float
    label: str
    key: str = "custom"
    rolling: bool = False   # "last hour"-style windows: exact length, never scaled to activity

    @property
    def hours(self) -> float:
        return (self.end - self.start) / 3600.0

    def live(self, now: float) -> bool:
        return self.end > now

    def asof(self, now: float) -> float:
        """The instant 'current state' questions (waiting now, in production) are answered for."""
        return min(self.end, now)

    def to_dict(self, now: float | None = None) -> dict:
        return {"start": self.start, "end": self.end, "label": self.label, "key": self.key,
                "live": self.live(now if now is not None else time.time())}


def _local_midnight(ts: float) -> datetime:
    return datetime.fromtimestamp(ts).replace(hour=0, minute=0, second=0, microsecond=0)


def _ts(d: datetime) -> float:
    return d.timestamp()


def _hm(text: str) -> tuple[int, int]:
    h, m = text.split(":")
    return int(h), int(m)


def _shift_windows(shifts: list[dict], around: float) -> list[tuple[float, float, str]]:
    """Every shift occurrence starting from 2 days before to 1 day after `around`, sorted."""
    out = []
    base = _local_midnight(around)
    for d in range(-2, 2):
        day = base + timedelta(days=d)
        for s in shifts:
            h, m = _hm(s["start"])
            eh, em = _hm(s["end"])
            start = day.replace(hour=h, minute=m)
            end = day.replace(hour=eh, minute=em)
            if end <= start:
                end += timedelta(days=1)
            out.append((_ts(start), _ts(end), s["name"]))
    return sorted(out)


def shift_of(ts: float, shifts: list[dict]) -> str:
    """Name of the shift a moment falls in ('Off shift' when the shifts leave a gap)."""
    for a, b, name in _shift_windows(shifts, ts):
        if a <= ts < b:
            return name
    return "Off shift"


def resolve(key: str, now: float, shifts: list[dict], start: float | str | None = None,
            end: float | str | None = None) -> Window:
    key = (key or "today").lower()
    if key not in PERIODS:
        raise PeriodError(f"unknown period '{key}' (use one of: {', '.join(PERIODS)})")
    today = _local_midnight(now)
    if key == "last_hour":
        return Window(now - 3600, now, "Last hour", key, rolling=True)
    if key == "last_24h":
        return Window(now - 86400, now, "Last 24 hours", key, rolling=True)
    if key == "last_7d":
        return Window(_ts(today - timedelta(days=6)), _ts(today + timedelta(days=1)), "Last 7 days", key)
    if key == "last_30d":
        return Window(_ts(today - timedelta(days=29)), _ts(today + timedelta(days=1)), "Last 30 days", key)
    if key == "today":
        return Window(_ts(today), _ts(today + timedelta(days=1)), "Today", key)
    if key == "yesterday":
        return Window(_ts(today - timedelta(days=1)), _ts(today), "Yesterday", key)
    if key in ("this_week", "last_week"):
        monday = today - timedelta(days=today.weekday())
        if key == "this_week":
            return Window(_ts(monday), _ts(monday + timedelta(days=7)), "This week", key)
        return Window(_ts(monday - timedelta(days=7)), _ts(monday), "Last week", key)
    if key in ("this_month", "last_month"):
        first = today.replace(day=1)
        nxt = (first + timedelta(days=32)).replace(day=1)
        if key == "this_month":
            return Window(_ts(first), _ts(nxt), "This month", key)
        prev = (first - timedelta(days=1)).replace(day=1)
        return Window(_ts(prev), _ts(first), "Last month", key)
    if key in ("current_shift", "previous_shift"):
        wins = _shift_windows(shifts, now)
        cur = next(((a, b, n) for a, b, n in wins if a <= now < b), None)
        if key == "current_shift":
            if cur is None:  # gap between shifts: report the one that ended last
                cur = max((w for w in wins if w[0] <= now), key=lambda w: w[0])
                return Window(cur[0], cur[1], f"{cur[2]} shift (ended)", key)
            return Window(cur[0], cur[1], f"{cur[2]} shift", key)
        ref = cur[0] if cur else max(w[0] for w in wins if w[0] <= now)
        prev = max((w for w in wins if w[0] < ref), key=lambda w: w[0])
        return Window(prev[0], prev[1], f"{prev[2]} shift (previous)", key)
    # custom
    a, b = _parse_moment(start, "start"), _parse_moment(end, "end", end_of_day=True)
    if a is None or b is None:
        raise PeriodError("a custom period needs start and end (epoch seconds or YYYY-MM-DD)")
    if b <= a:
        raise PeriodError("the end of the period must be after its start")
    if b - a > MAX_SPAN_DAYS * 86400:
        raise PeriodError(f"a custom period can span at most {MAX_SPAN_DAYS} days")
    fmt = "%Y-%m-%d"
    return Window(a, b, f"{datetime.fromtimestamp(a).strftime(fmt)} to {datetime.fromtimestamp(b - 1).strftime(fmt)}",
                  "custom")


def _parse_moment(v, what: str, end_of_day: bool = False) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        pass
    try:
        d = datetime.strptime(str(v)[:10], "%Y-%m-%d")
    except ValueError:
        raise PeriodError(f"{what} must be epoch seconds or YYYY-MM-DD") from None
    return _ts(d + timedelta(days=1)) if end_of_day else _ts(d)


def previous(w: Window, now: float, shifts: list[dict], like_for_like: bool = True) -> Window:
    """The period to compare `w` against. A live window is compared with the previous period cut
    at the same elapsed time, and the label says so."""
    if w.key in ("today", "yesterday", "this_week", "last_week", "this_month", "last_month",
                 "current_shift", "previous_shift"):
        pair = {"today": "yesterday", "yesterday": None, "this_week": "last_week", "last_week": None,
                "this_month": "last_month", "last_month": None,
                "current_shift": "previous_shift", "previous_shift": None}[w.key]
        if pair:
            p = resolve(pair, now, shifts)
        else:  # compare a finished period with the one before it, same length
            span = w.end - w.start
            if w.key == "yesterday":
                p = replace(resolve("today", now - 86400 * 2, shifts), label="Day before")
            elif w.key == "last_week":
                p = Window(w.start - 7 * 86400, w.start, "Week before", "custom")
            elif w.key == "last_month":
                first = datetime.fromtimestamp(w.start).replace(day=1)
                pm = (first - timedelta(days=1)).replace(day=1)
                p = Window(_ts(pm), _ts(first), "Month before", "custom")
            else:
                p = Window(w.start - span, w.start, "Shift before", "custom")
    elif w.key in ("last_hour", "last_24h"):
        span = w.end - w.start
        p = Window(w.start - span, w.start, "Hour before" if w.key == "last_hour" else "24 hours before", "custom", True)
    else:
        span = w.end - w.start
        p = Window(w.start - span, w.start, "Previous period", "custom")
    if like_for_like and w.live(now) and not w.rolling:
        elapsed = now - w.start
        if elapsed < (p.end - p.start):
            hh = datetime.fromtimestamp(p.start + elapsed).strftime("%H:%M")
            p = Window(p.start, p.start + elapsed, f"{p.label} up to {hh}" if w.key != "this_month"
                       and w.key != "this_week" else f"{p.label} (same elapsed time)", p.key)
    return p
