"""Reality Connector - Phase 8: shelf / inventory monitoring.

A standalone process for a camera dedicated to watching one or more shelves.
It samples the feed every `SHELF_CHECK_INTERVAL_S` (not every frame - shelves
don't move), counts the configured object class(es) inside each shelf's
region, and writes shelf_checks / shelf_alerts into the same events.db the
dashboard (Phase 5) reads. Add shelves first via the API (see README).

    python -m phase8_shelf.app
    python -m phase8_shelf.app --no-display --duration 3600
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

from .checker import ShelfChecker
from .config import Phase8Config
from .counter import BottleCounter


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Reality Connector - Phase 8 shelf monitoring")
    p.add_argument("--no-display", action="store_true", help="headless: no window, log only")
    p.add_argument("--duration", type=float, default=0, help="stop after N seconds (0 = forever)")
    p.add_argument("--once", action="store_true", help="run a single check and exit (for testing)")
    p.add_argument("--env")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    cfg = Phase8Config.load(args.env)
    log = get_logger("reality_connector.phase8", cfg.log_level, str(cfg.log_dir))

    stream = LatestFrameStream(CameraStream(cfg.camera, log), log)
    counter = BottleCounter(cfg, log)
    store = EventStore(cfg.events_db)
    checker = ShelfChecker(cfg, store, counter, log)

    log.info("Warming up model ...")
    counter.warmup()

    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("flag", True))
    try:
        signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("flag", True))
    except (AttributeError, ValueError):
        pass

    if not stream.start():
        log.error("Could not connect to the shelf camera (SHELF_* settings in .env). Exiting.")
        return 1

    window = "Reality Connector - Phase 8 (shelf monitoring)"
    display = not args.no_display
    if display:
        try:
            cv2.namedWindow(window, cv2.WINDOW_NORMAL)
        except cv2.error:
            log.warning("OpenCV has no GUI support - running headless.")
            display = False

    start = time.time()
    last_check = 0.0
    exit_code = 0
    try:
        while not stop["flag"]:
            frame, _ = stream.read_latest(-1)  # always "new" - we poll on our own schedule
            if frame is None:
                if not (stream._thread and stream._thread.is_alive()):
                    log.error("Capture thread stopped. Exiting.")
                    exit_code = 1
                    break
                time.sleep(0.2)
                continue

            now = time.time()
            if now - last_check >= cfg.check_interval_s:
                checker.run_once(frame)
                last_check = now
                if args.once:
                    break

            if display:
                cv2.putText(frame, f"checks={checker.checks_run} alerts={checker.alerts_raised} "
                                  f"next in {max(0, cfg.check_interval_s - (now - last_check)):.0f}s",
                           (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 4, cv2.LINE_AA)
                cv2.putText(frame, f"checks={checker.checks_run} alerts={checker.alerts_raised} "
                                  f"next in {max(0, cfg.check_interval_s - (now - last_check)):.0f}s",
                           (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 1, cv2.LINE_AA)
                cv2.imshow(window, frame)
                if (cv2.waitKey(1) & 0xFF) in (ord("q"), 27):
                    break
                try:
                    if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                        break
                except cv2.error:
                    break
            else:
                time.sleep(0.5)

            if args.duration and (now - start) >= args.duration:
                log.info("Duration limit (%.0f s) reached.", args.duration)
                break
    finally:
        stream.stop()
        store.close()
        if display:
            cv2.destroyAllWindows()
        log.info("Done. %d check(s) run, %d alert(s) raised.", checker.checks_run, checker.alerts_raised)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
