"""Reality Connector - Phase 1 live viewer.

    python -m phase1_camera.app                 # live window, 'q' or Esc to quit
    python -m phase1_camera.app --no-display --duration 3600   # 1 h headless soak test
    python -m phase1_camera.app --snapshot frame.jpg           # grab one frame and exit
"""
from __future__ import annotations

import argparse
import signal
import sys
import time

import cv2

from .camera import CameraStream
from .config import Config
from .logger import get_logger


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Reality Connector - Phase 1 Hikvision live viewer")
    p.add_argument("--no-display", action="store_true",
                   help="headless: no window, log metrics only (stability / soak test)")
    p.add_argument("--duration", type=float, default=0,
                   help="stop after N seconds (0 = run until 'q' / window closed / Ctrl+C)")
    p.add_argument("--metrics-interval", type=float, default=10,
                   help="seconds between metric log lines (default 10)")
    p.add_argument("--snapshot", metavar="PATH",
                   help="save the first valid frame to PATH, then exit")
    p.add_argument("--env", help="path to an alternate .env file")
    return p.parse_args(argv)


def _draw_overlay(frame, status: dict) -> None:
    lines = [
        f"{status['resolution']}   {status['actual_fps']} fps",
        f"recv {status['frames_received']}  fail {status['frames_failed']}  reconn {status['reconnections']}",
        f"uptime {status['stream_uptime_s']} s",
    ]
    y = 26
    for text in lines:
        cv2.putText(frame, text, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(frame, text, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 1, cv2.LINE_AA)
        y += 26


def main(argv=None) -> int:
    args = parse_args(argv)
    cfg = Config.load(args.env)
    log = get_logger(level=cfg.log_level, log_dir=str(cfg.log_dir))

    if cfg.uses_rtsp and not cfg.camera_ip:
        log.error("CAMERA_IP is not set. Copy phase1_camera/.env.example to .env and fill it in.")
        return 2

    cam = CameraStream(cfg, log)

    stop = {"flag": False}

    def _handle_signal(signum, _frame):
        log.info("Shutdown signal (%s) received.", signum)
        stop["flag"] = True
        cam.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    try:
        signal.signal(signal.SIGTERM, _handle_signal)
    except (AttributeError, ValueError):
        pass

    if not cam.open():
        log.error("Could not establish the initial connection. Exiting.")
        return 1

    window = "Reality Connector - Phase 1 (Hikvision)"
    display = not args.no_display
    if display:
        try:
            cv2.namedWindow(window, cv2.WINDOW_NORMAL)
        except cv2.error:
            log.warning(
                "OpenCV has no GUI support (headless build, or the venv is not active) - "
                "running without a window. Install 'opencv-python' (not '-headless') in an "
                "activated venv, or use --snapshot / --no-display."
            )
            display = False

    start = time.time()
    last_metrics = 0.0
    target_dt = 1.0 / cfg.target_fps if cfg.target_fps > 0 else 0.0
    exit_code = 0

    try:
        while not stop["flag"]:
            loop_start = time.time()

            ok, frame = cam.read_frame()
            if not ok:
                if cam.should_reconnect:
                    log.warning("Read retry limit reached - reconnecting.")
                    if not cam.reconnect():
                        log.error("Reconnect failed permanently. Exiting.")
                        exit_code = 1
                        break
                else:
                    time.sleep(0.05)
                continue

            if args.snapshot:
                cv2.imwrite(args.snapshot, frame)
                log.info("Snapshot saved to %s (%dx%d).", args.snapshot, frame.shape[1], frame.shape[0])
                break

            status = cam.get_status()
            now = time.time()
            if now - last_metrics >= args.metrics_interval:
                log.info("metrics %s", status)
                last_metrics = now

            if display:
                _draw_overlay(frame, status)
                cv2.imshow(window, frame)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):  # q or Esc
                    log.info("Quit key pressed.")
                    break
                try:
                    if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                        log.info("Window closed by user.")
                        break
                except cv2.error:
                    break

            if args.duration and (now - start) >= args.duration:
                log.info("Duration limit (%.0f s) reached.", args.duration)
                break

            if target_dt:
                remaining = target_dt - (time.time() - loop_start)
                if remaining > 0:
                    time.sleep(remaining)
    finally:
        final_status = cam.get_status()
        cam.release()
        if display:
            cv2.destroyAllWindows()
        log.info("Final status: %s", final_status)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
