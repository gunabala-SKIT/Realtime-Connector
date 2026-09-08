"""Overlay rendering for the Phase 2 viewer."""
from __future__ import annotations

import colorsys

import cv2

_FONT = cv2.FONT_HERSHEY_SIMPLEX


def track_color(track_id: int | None) -> tuple[int, int, int]:
    """Stable, well-spread BGR colour per track id."""
    if track_id is None:
        return (150, 150, 150)
    hue = (track_id * 0.61803398875) % 1.0  # golden-ratio hopping
    r, g, b = colorsys.hsv_to_rgb(hue, 0.65, 1.0)
    return (int(b * 255), int(g * 255), int(r * 255))


def draw_tracks(frame, tracks, stats: dict) -> None:
    for t in tracks:
        col = track_color(t.track_id)
        cv2.rectangle(frame, (t.x1, t.y1), (t.x2, t.y2), col, 2)
        label = f"person {t.conf:.2f}" if t.track_id is None else f"ID {t.track_id}  {t.conf:.2f}"
        (tw, th), _ = cv2.getTextSize(label, _FONT, 0.5, 1)
        top = max(0, t.y1 - th - 6)
        cv2.rectangle(frame, (t.x1, top), (t.x1 + tw + 6, top + th + 6), col, -1)
        cv2.putText(frame, label, (t.x1 + 3, top + th + 1), _FONT, 0.5, (0, 0, 0), 1, cv2.LINE_AA)

    lines = [
        f"{stats['resolution']}   disp {stats['disp_fps']:.1f} fps   AI {stats['ai_fps']:.1f} fps "
        f"({stats['infer_ms']:.0f} ms)",
        f"persons now: {stats['persons']}    unique track IDs: {stats['unique_tracks']}",
        f"{stats['device']} | {stats['model']} | {stats['tracker']}",
    ]
    y = 22
    for ln in lines:
        cv2.putText(frame, ln, (10, y), _FONT, 0.55, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(frame, ln, (10, y), _FONT, 0.55, (0, 255, 0), 1, cv2.LINE_AA)
        y += 24
