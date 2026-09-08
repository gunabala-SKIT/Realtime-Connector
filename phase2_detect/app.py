"""Reality Connector - Phase 2 viewer: person detection + tracking.

    python -m phase2_detect.app                       # live window
    python -m phase2_detect.app --no-display --duration 600
    python -m phase2_detect.app --save out.mp4        # write annotated video
"""
from __future__ import annotations

import argparse
import signal
import sys
import time
from collections import deque

import cv2

from phase1_camera.camera import CameraStream
from phase1_camera.logger import get_logger

from .config import Phase2Config
from .detector import PersonTracker
from .draw import draw_tracks


class _Rate:
    def __init__(self, n: int = 60) -> None:
        self.t = deque(maxlen=n)

    def tick(self) -> None:
        self.t.append(time.perf_counter())

    @property
    def fps(self) -> float:
        if len(self.t) < 2:
            return 0.0
        dt = self.t[-1] - self.t[0]
        return (len(self.t) - 1) / dt if dt > 0 else 0.0


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Reality Connector - Phase 2 person detection + tracking")
    p.add_argument("--no-display", action="store_true", help="headless: no window, log metrics only")
    p.add_argument("--duration", type=float, default=0, help="stop after N seconds (0 = until quit)")
    p.add_argument("--metrics-interval", type=float, default=10)
    p.add_argument("--save", metavar="PATH", help="write the annotated stream to an .mp4")
    p.add_argument("--env", help="path to an alternate .env file")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    cfg = Phase2Config.load(args.env)
    log = get_logger("reality_connector.phase2", cfg.log_level, str(cfg.log_dir))

    cam = CameraStream(cfg.camera, log)
    tracker = PersonTracker(cfg, log)

    stop = {"flag": False}

    def _sig(signum, _frame):
        log.info("Shutdown signal (%s).", signum)
        stop["flag"] = True
        cam.stop()

    signal.signal(signal.SIGINT, _sig)
    try:
        signal.signal(signal.SIGTERM, _sig)
    except (AttributeError, ValueError):
        pass

    if not cam.open():
        log.error("Could not connect to the camera source. Exiting.")
        return 1

    window = "Reality Connector - Phase 2 (detection + tracking)"
    display = not args.no_display
    if display:
        try:
            cv2.namedWindow(window, cv2.WINDOW_NORMAL)
        except cv2.error:
            log.warning("OpenCV has no GUI support - running headless. Use --save to keep output.")
            display = False

    writer = None
    unique_ids: set[int] = set()
    disp_rate, ai_rate = _Rate(), _Rate()
    start = time.time()
    last_metrics = 0.0
    exit_code = 0

    try:
        while not stop["flag"]:
            ok, frame = cam.read_frame()
            if not ok:
                if cam.should_reconnect:
                    log.warning("Read retry limit reached - reconnecting.")
                    if not cam.reconnect():
                        exit_code = 1
                        break
                else:
                    time.sleep(0.02)
                continue

            tracks = tracker.process(frame)
            ai_rate.tick()
            disp_rate.tick()

            persons = 0
            for t in tracks:
                if t.cls == 0:
                    persons += 1
                if t.track_id is not None:
                    unique_ids.add(t.track_id)

            h, w = frame.shape[:2]
            stats = {
                "resolution": f"{w}x{h}",
                "disp_fps": disp_rate.fps,
                "ai_fps": ai_rate.fps,
                "infer_ms": tracker.last_infer_ms,
                "persons": persons,
                "unique_tracks": len(unique_ids),
                "device": tracker.device,
                "model": cfg.model,
                "tracker": cfg.tracker.replace(".yaml", ""),
            }

            now = time.time()
            if now - last_metrics >= args.metrics_interval:
                log.info(
                    "persons=%d unique_ids=%d ai_fps=%.1f infer_ms=%.0f cam=%s",
                    persons, len(unique_ids), stats["ai_fps"], stats["infer_ms"],
                    cam.get_status()["actual_fps"],
                )
                last_metrics = now

            draw_tracks(frame, tracks, stats)

            if args.save:
                if writer is None:
                    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                    fps = cam.get_status().get("actual_fps") or 15.0
                    writer = cv2.VideoWriter(args.save, fourcc, max(fps, 5.0), (w, h))
                    log.info("Recording annotated video to %s", args.save)
                writer.write(frame)

            if display:
                cv2.imshow(window, frame)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
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
        cam_status = cam.get_status()
        cam.release()
        if writer is not None:
            writer.release()
        if display:
            cv2.destroyAllWindows()
        log.info(
            "Done. unique track IDs seen: %d | camera: %s",
            len(unique_ids), cam_status,
        )

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
