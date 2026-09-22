"""Reality Connector - Phase 13: vehicle entry/exit tracking via license
plate OCR.

A standalone process for a camera dedicated to watching a gate/entrance for
vehicles. Runs vehicle detection+tracking on every frame and attempts a
license-plate OCR read on each not-yet-confirmed tracked vehicle on its own
schedule; once enough readings agree (majority vote), writes one
vehicle_event into the same events.db the dashboard (Phase 5) reads. Tag
cameras as entry/exit from the dashboard's Attendance panel (reused from
Phase 11) to get cross-camera entry/exit tracking, same as employees.

    python -m phase13_vehicle.app
    python -m phase13_vehicle.app --no-display --duration 3600
"""
from __future__ import annotations

import argparse
import signal
import sys
import time

import cv2

from phase1_camera.camera import CameraStream
from phase1_camera.logger import get_logger
from phase2_detect.stream import LatestFrameStream
from phase4_events.store import EventStore

from .config import Phase13Config
from .tracker import VehicleTracker


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Reality Connector - Phase 13 vehicle tracking")
    p.add_argument("--no-display", action="store_true", help="headless: no window, log only")
    p.add_argument("--duration", type=float, default=0, help="stop after N seconds (0 = forever)")
    p.add_argument("--env")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    cfg = Phase13Config.load(args.env)
    log = get_logger("reality_connector.phase13", cfg.log_level, str(cfg.log_dir))

    stream = LatestFrameStream(CameraStream(cfg.camera, log), log)
    store = EventStore(cfg.events_db)
    vt = VehicleTracker(cfg, store, log)

    log.info("Warming up models ...")
    vt.warmup()

    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("flag", True))
    try:
        signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("flag", True))
    except (AttributeError, ValueError):
        pass

    if not stream.start():
        log.error("Could not connect to the vehicle camera (VEHICLE_* settings in .env). Exiting.")
        return 1

    window = "Reality Connector - Phase 13 (vehicle tracking)"
    display = not args.no_display
    if display:
        try:
            cv2.namedWindow(window, cv2.WINDOW_NORMAL)
        except cv2.error:
            log.warning("OpenCV has no GUI support - running headless.")
            display = False

    start = time.time()
    exit_code = 0
    try:
        while not stop["flag"]:
            frame, _ = stream.read_latest(-1)
            if frame is None:
                if not (stream._thread and stream._thread.is_alive()):
                    log.error("Capture thread stopped. Exiting.")
                    exit_code = 1
                    break
                time.sleep(0.05)
                continue

            vt.process_frame(frame)
            now = time.time()

            if display:
                label = f"vehicle events logged: {vt.events_logged}"
                cv2.putText(frame, label, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                           (0, 0, 0), 4, cv2.LINE_AA)
                cv2.putText(frame, label, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                           (0, 255, 0), 1, cv2.LINE_AA)
                cv2.imshow(window, frame)
                if (cv2.waitKey(1) & 0xFF) in (ord("q"), 27):
                    break
                try:
                    if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                        break
                except cv2.error:
                    break

            if args.duration and (now - start) >= args.duration:
                log.info("Duration limit (%.0f s) reached.", args.duration)
                break
    finally:
        stream.stop()
        store.close()
        if display:
            cv2.destroyAllWindows()
        log.info("Done. %d vehicle event(s) logged.", vt.events_logged)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
