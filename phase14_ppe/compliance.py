"""Per-person PPE decisions and the multi-check confirmation that turns them
into violations. Pure logic - no model, camera or database - so it can be
tested on synthetic boxes and on labelled dataset images alike.

The detector sees item boxes (helmet, vest, gloves, ...) and explicit "no_*"
boxes (a bare head, bare hands) anywhere in the frame. Each is attributed to a
tracked person by where its centre falls inside that person's box: a helmet
counts only in the head band, a vest only on the torso, and so on - a helmet
on the next person, or one held in a hand, must not count.
"""
from __future__ import annotations

from dataclasses import dataclass, field

Box = tuple[float, float, float, float]  # x1, y1, x2, y2 in pixels

WORN, MISSING, NOT_SEEN, UNKNOWN = "worn", "missing", "not_seen", "unknown"

# Where each item's box centre must fall, as fractions of the person box
# (x from its left edge, y from its top). Helmets and goggles may poke slightly
# above a person box; gloves reach out sideways with the arms.
REGIONS: dict[str, tuple[float, float, float, float]] = {
    "helmet": (0.0, -0.15, 1.0, 0.35),
    "goggles": (0.0, -0.05, 1.0, 0.35),
    "vest": (0.0, 0.15, 1.0, 0.80),
    "gloves": (-0.25, 0.20, 1.25, 1.00),
    "boots": (-0.10, 0.70, 1.10, 1.10),
}

# Detector class -> (item, is_negative). "no_*" classes are explicit evidence
# the item is absent. The dataset's oddly named "none" class is its no-vest
# label: it's drawn over bare torsos (checked against the images - there is
# no "no_vest" class).
CLASS_MAP: dict[str, tuple[str, bool]] = {
    "helmet": ("helmet", False), "no_helmet": ("helmet", True),
    "vest": ("vest", False), "none": ("vest", True),
    "gloves": ("gloves", False), "no_gloves": ("gloves", True),
    "boots": ("boots", False), "no_boots": ("boots", True),
    "goggles": ("goggles", False), "no_goggle": ("goggles", True),
}

# Items that are large and normally visible on anyone in frame, so "nothing
# detected" is itself evidence. Hands and feet are often hidden (pockets,
# desks, behind others) - for those, only an explicit "no_*" detection counts.
ABSENCE_IS_EVIDENCE = {"helmet", "vest"}


@dataclass(frozen=True)
class Detection:
    cls: str
    conf: float
    box: Box


def _centre(box: Box) -> tuple[float, float]:
    return (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0


def _in_region(det: Detection, person: Box, item: str) -> bool:
    x1, y1, x2, y2 = person
    w, h = max(x2 - x1, 1.0), max(y2 - y1, 1.0)
    rx1, ry1, rx2, ry2 = REGIONS[item]
    cx, cy = _centre(det.box)
    return x1 + rx1 * w <= cx <= x1 + rx2 * w and y1 + ry1 * h <= cy <= y1 + ry2 * h


OCCLUDED_FRACTION = 0.4  # this much of a person's box covered by someone else's -> can't judge absence


def covered_fraction(person: Box, others: list[Box]) -> float:
    """Largest share of `person`'s box covered by any one other person's box."""
    area = max((person[2] - person[0]) * (person[3] - person[1]), 1.0)
    best = 0.0
    for o in others:
        ix = max(0.0, min(person[2], o[2]) - max(person[0], o[0]))
        iy = max(0.0, min(person[3], o[3]) - max(person[1], o[1]))
        best = max(best, ix * iy / area)
    return best


def assess_person(person: Box, detections: list[Detection], required: list[str],
                  frame_h: int, min_person_px: int = 80, others: list[Box] = ()) -> dict[str, str]:
    """Status of each required item for one person: worn | missing | not_seen | unknown.

    unknown = can't judge: the person is too small, their head is cut off by the
    top of the frame, or they're largely hidden behind someone else (`others` =
    the other people's boxes). Hidden or cut off, "no helmet seen" isn't
    evidence - measured on the test split, occlusion caused over half of all
    helmet/vest false alarms. Explicit evidence (a visible bare head) still counts."""
    x1, y1, x2, y2 = person
    too_small = (y2 - y1) < min_person_px
    head_cut = y1 <= 2  # box touches the top edge
    occluded = covered_fraction(person, others) > OCCLUDED_FRACTION
    out: dict[str, str] = {}
    for item in required:
        if too_small or (head_cut and item in ("helmet", "goggles")):
            out[item] = UNKNOWN
            continue
        pos = max((d.conf for d in detections
                   if CLASS_MAP.get(d.cls) == (item, False) and _in_region(d, person, item)), default=0.0)
        neg = max((d.conf for d in detections
                   if CLASS_MAP.get(d.cls) == (item, True) and _in_region(d, person, item)), default=0.0)
        if pos and pos >= neg:
            out[item] = WORN
        elif neg:
            out[item] = MISSING
        else:
            out[item] = UNKNOWN if occluded else NOT_SEEN
    return out


def failing_items(status: dict[str, str]) -> tuple[list[str], list[str]]:
    """-> (items failing this check, items passing this check). Items whose
    status is no evidence either way (unknown, or not_seen for hands/feet) are
    in neither list, so they neither advance nor reset the confirmation count."""
    fail, ok = [], []
    for item, s in status.items():
        if s == MISSING or (s == NOT_SEEN and item in ABSENCE_IS_EVIDENCE):
            fail.append(item)
        elif s == WORN:
            ok.append(item)
    return fail, ok


@dataclass
class _TrackState:
    streak: dict[str, int] = field(default_factory=dict)       # item -> consecutive failing checks
    ok_streak: dict[str, int] = field(default_factory=dict)    # item -> consecutive passing checks
    last_logged: dict[str, float] = field(default_factory=dict)  # item -> ts of last violation
    last_seen: float = 0.0


class ViolationTracker:
    """Only confirms a violation once an item has failed `confirm_checks`
    consecutive checks for the same tracked person - one frame where a helmet
    is hidden by a raised arm must not raise an alarm. Items that confirm on
    the same check are reported together; the same person/item isn't logged
    again until `cooldown_s` has passed - unless the item has been worn for
    `confirm_checks` consecutive checks in between: putting the helmet back on
    and taking it off again is a new incident. That also covers the tracker
    handing an old track id to a different person standing in the same spot."""

    def __init__(self, confirm_checks: int = 3, cooldown_s: float = 300.0, stale_s: float = 30.0) -> None:
        self.confirm_checks = confirm_checks
        self.cooldown_s = cooldown_s
        self.stale_s = stale_s
        self._tracks: dict[int, _TrackState] = {}

    def update(self, track_id: int, status: dict[str, str], now: float) -> list[str]:
        """Record one check for one person; returns the items that just became a confirmed violation."""
        st = self._tracks.setdefault(track_id, _TrackState())
        st.last_seen = now
        fail, ok = failing_items(status)
        for item in ok:
            st.streak[item] = 0
            st.ok_streak[item] = st.ok_streak.get(item, 0) + 1
            if st.ok_streak[item] >= self.confirm_checks:
                st.last_logged.pop(item, None)  # complied - the next lapse is a new violation
        confirmed = []
        for item in fail:
            st.ok_streak[item] = 0
            st.streak[item] = st.streak.get(item, 0) + 1
            recently = now - st.last_logged.get(item, float("-inf")) < self.cooldown_s
            if st.streak[item] >= self.confirm_checks and not recently:
                confirmed.append(item)
        for item in confirmed:
            st.last_logged[item] = now
        return confirmed

    def streak(self, track_id: int, item: str) -> int:
        st = self._tracks.get(track_id)
        return st.streak.get(item, 0) if st else 0

    def prune(self, now: float) -> None:
        for tid in [t for t, st in self._tracks.items() if now - st.last_seen > self.stale_s]:
            del self._tracks[tid]
