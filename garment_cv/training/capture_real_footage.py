"""Captures real frames from the live camera for real training data (spec section 7), tagged by
which activity/object the person in front of the camera was doing when each frame was grabbed.

This only captures and tags raw frames by folder - it does NOT draw bounding boxes. That's a
separate, deliberately manual step (see `garment_cv/training/README.md`'s "real data" section):
for `person` (already COCO-detectable) boxes can be bootstrapped from the existing model, but the
7 classes with no COCO match (fabric, marking_tool, measuring_scale, pattern, sewing_machine,
fabric_piece, bundle) have no shortcut - there is no existing detector for them to bootstrap from.

    python -m garment_cv.training.capture_real_footage --label marking_chalk --seconds 8
    python -m garment_cv.training.capture_real_footage --label cutting_active --seconds 10 --fps 2

Reads the camera source the same way the main app does (garment_cv/configs/.env), so whatever
`GARMENT_CAMERA_SOURCE`/`_USER`/`_PASSWORD` the live app uses is exactly what this captures from -
no separate camera config to keep in sync.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2

from ..backend.cameras.config import CameraConfig

HERE = Path(__file__).resolve().parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, help="subfolder name - what's happening in this clip, "
                                                    "e.g. marking_chalk, cutting_active, cutting_holding_only")
    ap.add_argument("--seconds", type=float, default=8.0, help="how long to capture")
    ap.add_argument("--fps", type=float, default=2.0, help="frames saved per second (not the camera's own fps)")
    ap.add_argument("--out", default=str(HERE / "datasets" / "real" / "raw"))
    args = ap.parse_args()

    cfg = CameraConfig.load()
    if not cfg.source:
        raise SystemExit("No camera configured - set GARMENT_CAMERA_SOURCE in garment_cv/configs/.env first.")

    out_dir = Path(args.out) / args.label
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = len(list(out_dir.glob("*.jpg")))

    import os
    if cfg.is_network_url:
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = cfg.ffmpeg_capture_options()
    source = cfg.opencv_source()
    cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG) if cfg.is_network_url else cv2.VideoCapture(source)
    if not cap.isOpened():
        raise SystemExit(f"Could not open camera source: {cfg.safe_source}")

    print(f"Capturing '{args.label}' for {args.seconds:.0f}s at {args.fps:.1f} saved-fps from {cfg.safe_source}")
    interval = 1.0 / args.fps
    deadline = time.monotonic() + args.seconds
    saved = 0
    next_save = time.monotonic()
    while time.monotonic() < deadline:
        ok, frame = cap.read()
        if not ok:
            continue
        now = time.monotonic()
        if now >= next_save:
            path = out_dir / f"{args.label}_{existing + saved:04d}.jpg"
            cv2.imwrite(str(path), frame)
            saved += 1
            next_save = now + interval
    cap.release()
    print(f"Saved {saved} frame(s) to {out_dir}")


if __name__ == "__main__":
    main()
