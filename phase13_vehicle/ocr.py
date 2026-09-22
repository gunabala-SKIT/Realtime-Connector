"""License-plate reading: generic OCR (EasyOCR) on a vehicle crop, filtered
to plate-shaped strings, with per-track majority-vote accumulation.

No dedicated plate-detector model is used - same "start with a generic,
pretrained capability" choice as Phase 8's bottle detection. EasyOCR reads
*any* text in the crop; `is_plate_like` filters out background text/noise
that isn't shaped like a plate.

Why majority-vote across several readings, not a single OCR call: testing
this in isolation found EasyOCR reliably misreads visually-ambiguous
characters on a clean synthetic plate (0 <-> O) even at high confidence -
a well-known, inherent OCR limitation, not a bug. A single frame's reading
cannot be trusted as-is. Phase 3 already established the fix for exactly
this kind of per-frame noise (multi-frame confirmation before committing an
identity); this applies the same idea to plates: accumulate several
readings from a tracked vehicle and commit the most-frequent exact string,
not whichever one happened to be read first.
"""
from __future__ import annotations

import re
import time

import numpy as np

_PLATE_RE = re.compile(r"^[A-Z0-9]{5,11}$")


def normalize_plate_text(raw: str) -> str:
    """Upper-case, strip everything but letters/digits (spaces, dashes,
    dots - plates are rendered with separators OCR may or may not pick up
    consistently, so drop them before comparing/storing)."""
    return re.sub(r"[^A-Z0-9]", "", raw.upper())


def is_plate_like(text: str) -> bool:
    """Reject obvious non-plate OCR noise: too short/long, pure letters
    (a word), or pure digits (could be anything). Real plates mix both."""
    if not _PLATE_RE.match(text):
        return False
    has_digit = any(c.isdigit() for c in text)
    has_letter = any(c.isalpha() for c in text)
    return has_digit and has_letter


class PlateReader:
    """Wraps easyocr.Reader - loaded lazily so importing this module (e.g.
    for the pure-logic unit tests) never requires a GPU or downloads."""

    def __init__(self, logger, gpu: bool = True) -> None:
        self.log = logger
        self._gpu = gpu
        self._reader = None

    def _ensure_reader(self):
        if self._reader is None:
            import easyocr
            self.log.info("Loading EasyOCR reader (gpu=%s) ...", self._gpu)
            self._reader = easyocr.Reader(["en"], gpu=self._gpu)
            self.log.info("EasyOCR ready.")
        return self._reader

    def warmup(self) -> None:
        """Load the model now (~15-20s) instead of stalling mid-loop on the
        first real vehicle detection."""
        reader = self._ensure_reader()
        blank = np.zeros((64, 256, 3), dtype=np.uint8)
        try:
            reader.readtext(blank)
        except Exception:  # noqa: BLE001 - warmup is best-effort
            pass

    def read_candidates(self, frame: np.ndarray, bbox: tuple[int, int, int, int],
                         min_conf: float = 0.3) -> list[tuple[str, float]]:
        """OCR the crop for `bbox`, normalised + filtered to plate-like
        strings only. Returns [] if nothing plate-shaped was found - that's
        an expected, common outcome (most of a vehicle isn't its plate),
        not an error."""
        x1, y1, x2, y2 = bbox
        h, w = frame.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 <= x1 or y2 <= y1:
            return []
        crop = frame[y1:y2, x1:x2]
        reader = self._ensure_reader()
        try:
            results = reader.readtext(crop)
        except Exception:  # noqa: BLE001 - OCR must never crash the tracking loop
            self.log.exception("OCR read failed")
            return []

        out: list[tuple[str, float]] = []
        for _box, text, conf in results:
            if conf < min_conf:
                continue
            norm = normalize_plate_text(text)
            if is_plate_like(norm):
                out.append((norm, float(conf)))
        return out


class PlateAccumulator:
    """Per-track reading history + majority-vote commit, and throttling of
    how often OCR is even attempted on a track that hasn't confirmed yet."""

    def __init__(self, min_readings: int = 3, stale_after_s: float = 30.0) -> None:
        self.min_readings = min_readings
        self.stale_after_s = stale_after_s
        self._readings: dict[int, list[tuple[str, float]]] = {}
        self._last_attempt: dict[int, float] = {}
        self._last_seen: dict[int, float] = {}
        self._confirmed: set[int] = set()

    def is_confirmed(self, track_id: int) -> bool:
        return track_id in self._confirmed

    def should_attempt_ocr(self, track_id: int, now: float, interval_s: float) -> bool:
        if track_id in self._confirmed:
            return False
        return now - self._last_attempt.get(track_id, 0.0) >= interval_s

    def add_reading(self, track_id: int, candidates: list[tuple[str, float]], now: float) -> None:
        self._last_attempt[track_id] = now
        self._last_seen[track_id] = now
        if candidates:
            self._readings.setdefault(track_id, []).extend(candidates)

    def touch(self, track_id: int, now: float) -> None:
        """Mark a track as still alive this frame even when OCR wasn't
        attempted (throttled) - keeps it from being pruned as stale."""
        self._last_seen[track_id] = now

    def ready_to_commit(self, track_id: int) -> bool:
        return len(self._readings.get(track_id, [])) >= self.min_readings

    def _vote(self, track_id: int) -> tuple[str, int, float] | None:
        readings = self._readings.get(track_id)
        if not readings:
            return None
        by_text: dict[str, list[float]] = {}
        for text, conf in readings:
            by_text.setdefault(text, []).append(conf)
        # most votes wins; ties broken by higher average confidence
        best_text = max(by_text, key=lambda t: (len(by_text[t]), sum(by_text[t]) / len(by_text[t])))
        confs = by_text[best_text]
        return best_text, len(confs), sum(confs) / len(confs)

    def commit(self, track_id: int) -> tuple[str, int, float] | None:
        """Commit now if enough readings have accumulated. Returns
        (plate_text, vote_count, avg_confidence) or None if not ready yet."""
        if not self.ready_to_commit(track_id):
            return None
        result = self._vote(track_id)
        if result:
            self._confirmed.add(track_id)
            self._readings.pop(track_id, None)
        return result

    def force_commit(self, track_id: int) -> tuple[str, int, float] | None:
        """Best-effort commit with whatever's been read so far, even below
        min_readings - used when a track is about to be pruned (vehicle
        left frame quickly) so a brief sighting isn't silently dropped."""
        result = self._vote(track_id)
        if result:
            self._confirmed.add(track_id)
        self._readings.pop(track_id, None)
        return result

    def prune_stale(self, now: float | None = None) -> list[int]:
        """Drop bookkeeping for tracks not seen in a while (vehicle left
        frame). Returns the track_ids that still had unread-out readings
        and should be force-committed by the caller before being dropped."""
        now = now if now is not None else time.time()
        stale = [tid for tid, t in self._last_seen.items() if now - t > self.stale_after_s]
        pending = [tid for tid in stale if self._readings.get(tid)]
        for tid in stale:
            self._readings.pop(tid, None)
            self._last_attempt.pop(tid, None)
            self._last_seen.pop(tid, None)
            self._confirmed.discard(tid)
        return pending
