"""Minimal RTSP smoke test (Phase 1 doc section 13).

Deliberately small - the least code that proves OpenCV can open the stream and
decode frames. If this fails but VLC works, the problem is the decoder/backend,
not the camera or network (doc section 18).

    python -m phase1_camera.test_rtsp
"""
from __future__ import annotations

import os
import sys

import cv2

from .config import Config

MAX_FRAMES = 300


def main() -> int:
    cfg = Config.load()
    if cfg.uses_rtsp:
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = cfg.ffmpeg_capture_options()

    source = cfg.opencv_source()
    print(f"Opening: {cfg.safe_source}")

    cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG) if cfg.uses_rtsp else cv2.VideoCapture(source)
    if not cap.isOpened():
        print("FAIL: cap.isOpened() == False - could not open the stream.")
        return 1

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"OK: stream opened at {width}x{height}. Reading up to {MAX_FRAMES} frames (press 'q' to stop).")

    frames = 0
    while frames < MAX_FRAMES:
        ret, frame = cap.read()
        if not ret:
            print("Unable to read frame - stopping.")
            break
        frames += 1
        cv2.imshow("Reality Connector - RTSP smoke test (q to quit)", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()
    print(f"Read {frames} frame(s).")
    return 0 if frames > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
