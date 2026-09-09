"""Phase 3 overlay: person boxes coloured by identity state, name / status label,
face boxes, and an identity-state summary.
"""
from __future__ import annotations

import cv2

from .identity import IdentityState

_FONT = cv2.FONT_HERSHEY_SIMPLEX

# BGR - semantic colours, separate from any per-track hue
STATE_COLOR = {
    IdentityState.RECOGNIZED: (90, 190, 90),    # green
    IdentityState.UNKNOWN: (60, 60, 220),       # red
    IdentityState.UNVERIFIED: (60, 170, 220),   # amber
}


def _label(ti) -> str:
    if ti.state is IdentityState.RECOGNIZED:
        return f"{ti.employee_name}  ({ti.score:.2f})"
    if ti.state is IdentityState.UNKNOWN:
        return f"UNKNOWN  ({ti.best_score:.2f})"
    return "UNVERIFIED"


def draw_people(frame, tracks, identities, faces, stats: dict) -> None:
    # faces first, thin white
    for f in faces:
        cv2.rectangle(frame, (f.x1, f.y1), (f.x2, f.y2), (235, 235, 235), 1)

    for t in tracks:
        ti = identities.get(t.track_id)
        state = ti.state if ti else IdentityState.UNVERIFIED
        col = STATE_COLOR[state]
        cv2.rectangle(frame, (t.x1, t.y1), (t.x2, t.y2), col, 2)

        tag = f"ID {t.track_id}  {_label(ti)}" if ti else f"ID {t.track_id}  UNVERIFIED"
        (tw, th), _ = cv2.getTextSize(tag, _FONT, 0.5, 1)
        top = max(0, t.y1 - th - 6)
        cv2.rectangle(frame, (t.x1, top), (t.x1 + tw + 6, top + th + 6), col, -1)
        cv2.putText(frame, tag, (t.x1 + 3, top + th + 1), _FONT, 0.5, (0, 0, 0), 1, cv2.LINE_AA)

    c = stats["states"]
    lines = [
        f"{stats['resolution']}   proc {stats['proc_fps']:.1f} fps   "
        f"person {stats['person_ms']:.0f}ms  face {stats['face_ms']:.0f}ms",
        f"RECOGNIZED {c['RECOGNIZED']}   UNKNOWN {c['UNKNOWN']}   UNVERIFIED {c['UNVERIFIED']}   "
        f"| gallery: {stats['gallery']}",
    ]
    y = 22
    for ln in lines:
        cv2.putText(frame, ln, (10, y), _FONT, 0.55, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(frame, ln, (10, y), _FONT, 0.55, (0, 255, 0), 1, cv2.LINE_AA)
        y += 24
