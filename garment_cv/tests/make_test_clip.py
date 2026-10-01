"""A tiny synthetic video, so the camera module can be tested and demoed without real hardware
(the same reasoning as `manufacturing_intel/synth_video.py`: a real decodable file exercises the
actual OpenCV/FFmpeg read path, pacing and end-of-file looping - a mock object would not).

    python -m garment_cv.tests.make_test_clip out.mp4
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

FPS = 10
WIDTH, HEIGHT = 320, 240
DURATION_S = 4.0


def make_clip(path: str | Path, fps: int = FPS, width: int = WIDTH, height: int = HEIGHT,
             duration_s: float = DURATION_S) -> dict:
    path = Path(path)
    n_frames = int(fps * duration_s)
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    if not out.isOpened():
        raise RuntimeError("cv2.VideoWriter could not open the output file")
    for i in range(n_frames):
        frame = np.full((height, width, 3), 60, np.uint8)
        x = int((i / max(n_frames - 1, 1)) * (width - 40))
        cv2.rectangle(frame, (x, height // 2 - 20), (x + 40, height // 2 + 20), (60, 170, 60), -1)
        cv2.putText(frame, f"frame {i}", (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        out.write(frame)
    out.release()
    return {"fps": fps, "width": width, "height": height, "frames": n_frames, "duration_s": duration_s}


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "test_clip.mp4"
    meta = make_clip(target)
    print(f"wrote {target}: {meta}")
