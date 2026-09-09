"""Reality Connector - Phase 4 CLI viewer: identity + event generation.

Runs the VisionPipeline (which writes deduplicated events + snapshots) and shows
its annotated frames in a window. Run the API alongside to read events back:
`python -m phase4_events.api`.

    python -m phase4_events.app
    python -m phase4_events.app --no-display --duration 3600
"""
from __future__ import annotations

import argparse
import signal
import sys
import time

import cv2

from phase1_camera.logger import get_logger

from .config import Phase4Config
from .pipeline import VisionPipeline


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Reality Connector - Phase 4 identity + events")
    p.add_argument("--no-display", action="store_true")
    p.add_argument("--duration", type=float, default=0)
    p.add_argument("--metrics-interval", type=float, default=10)
    p.add_argument("--env")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    cfg = Phase4Config.load(args.env)
    log = get_logger("reality_connector.phase4", cfg.phase3.log_level, str(cfg.phase3.log_dir))

    pipeline = VisionPipeline(cfg, log)
    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("flag", True))
    try:
        signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("flag", True))
    except (AttributeError, ValueError):
        pass

    try:
        pipeline.start()
    except RuntimeError as e:
        log.error("%s. Exiting.", e)
        return 1

    window = "Reality Connector - Phase 4 (identity + events)"
    display = not args.no_display
    if display:
        try:
            cv2.namedWindow(window, cv2.WINDOW_NORMAL)
        except cv2.error:
            log.warning("OpenCV has no GUI support - running headless.")
            display = False

    start = time.time()
    last_metrics = 0.0
    try:
        while not stop["flag"] and pipeline.alive:
            frame = pipeline.latest_frame()
            now = time.time()

            if now - last_metrics >= args.metrics_interval:
                s = pipeline.status()
                c = s["states"]
                log.info("REC=%d UNK=%d UNV=%d | events=%d | proc_fps=%.1f person_ms=%d face_ms=%d",
                         c["RECOGNIZED"], c["UNKNOWN"], c["UNVERIFIED"], s["events"],
                         s["proc_fps"], s["person_ms"], s["face_ms"])
                last_metrics = now

            if display and frame is not None:
                cv2.imshow(window, frame)
                if (cv2.waitKey(1) & 0xFF) in (ord("q"), 27):
                    break
                try:
                    if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                        break
                except cv2.error:
                    break
            else:
                time.sleep(0.03)

            if args.duration and (now - start) >= args.duration:
                log.info("Duration limit (%.0f s) reached.", args.duration)
                break
    finally:
        pipeline.stop()
        if display:
            cv2.destroyAllWindows()
        log.info("Done. %d event(s) written to %s", pipeline.engine.count, cfg.events_db)

    return 0


if __name__ == "__main__":
    sys.exit(main())
