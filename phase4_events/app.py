"""Reality Connector - Phase 4 pipeline: identity + event generation.

Same vision pipeline as Phase 3, plus the event engine: identity-state
transitions per track become deduplicated rows in the events DB, with a
snapshot each. Run the API (`python -m phase4_events.api`) alongside to read
them back.

    python -m phase4_events.app
    python -m phase4_events.app --no-display --duration 3600
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
from phase2_detect.detector import PersonTracker
from phase2_detect.stream import LatestFrameStream
from phase3_identity.draw import draw_people
from phase3_identity.faces import FaceEngine
from phase3_identity.gallery import EmployeeGallery
from phase3_identity.identity import IdentityResolver

from .config import Phase4Config
from .engine import EventEngine
from .store import EventStore


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


def _face_in_person(face, person) -> bool:
    return person.x1 <= face.cx <= person.x2 and person.y1 <= face.cy <= person.y2


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Reality Connector - Phase 4 identity + events")
    p.add_argument("--no-display", action="store_true")
    p.add_argument("--duration", type=float, default=0)
    p.add_argument("--metrics-interval", type=float, default=10)
    p.add_argument("--save", metavar="PATH")
    p.add_argument("--env")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    cfg = Phase4Config.load(args.env)
    p3 = cfg.phase3
    log = get_logger("reality_connector.phase4", p3.log_level, str(p3.log_dir))

    stream = LatestFrameStream(CameraStream(p3.phase2.camera, log), log)
    tracker = PersonTracker(p3.phase2, log)
    faces = FaceEngine(p3, log)
    log.info("Warming up models ...")
    tracker.warmup()
    faces.warmup()

    gallery = EmployeeGallery(p3.gallery_db)
    resolver = IdentityResolver(p3, gallery, log)
    store = EventStore(cfg.events_db)
    engine = EventEngine(cfg, store, log)
    log.info("camera_id=%s  gallery=%d employees  events_db=%s",
             cfg.camera_id, len(gallery), cfg.events_db)
    if len(gallery) == 0:
        log.warning("Gallery empty - every person will raise an 'identity_unknown' event.")

    stop = {"flag": False}

    def _sig(signum, _frame):
        log.info("Shutdown signal (%s).", signum)
        stop["flag"] = True

    signal.signal(signal.SIGINT, _sig)
    try:
        signal.signal(signal.SIGTERM, _sig)
    except (AttributeError, ValueError):
        pass

    if not stream.start():
        log.error("Could not connect to the camera source. Exiting.")
        return 1

    window = "Reality Connector - Phase 4 (identity + events)"
    display = not args.no_display
    if display:
        try:
            cv2.namedWindow(window, cv2.WINDOW_NORMAL)
        except cv2.error:
            log.warning("OpenCV has no GUI support - running headless.")
            display = False

    writer = None
    proc_rate = _Rate()
    last_seq = 0
    start = time.time()
    last_metrics = 0.0
    exit_code = 0

    try:
        while not stop["flag"]:
            frame, seq = stream.read_latest(last_seq)
            if frame is None:
                if not (stream._thread and stream._thread.is_alive()):
                    log.error("Capture thread stopped. Exiting.")
                    exit_code = 1
                    break
                time.sleep(0.004)
                continue
            last_seq = seq
            resolver.begin_frame()

            t0 = time.perf_counter()
            people = [t for t in tracker.process(frame) if t.cls == 0]
            person_ms = (time.perf_counter() - t0) * 1000

            t1 = time.perf_counter()
            dets = faces.detect(frame)
            face_ms = (time.perf_counter() - t1) * 1000

            live_ids: set[int] = set()
            for person in people:
                if person.track_id is None:
                    continue
                live_ids.add(person.track_id)
                q_face = None
                if resolver.should_recognize(person.track_id):
                    inside = [f for f in dets if _face_in_person(f, person)]
                    if len(inside) == 1 and faces.quality(inside[0]).passed:
                        q_face = inside[0]
                resolver.update(person.track_id, q_face)

            resolver.prune(live_ids)
            engine.observe(resolver.tracks, live_ids, frame)
            proc_rate.tick()

            stats = {
                "resolution": f"{frame.shape[1]}x{frame.shape[0]}",
                "proc_fps": proc_rate.fps,
                "person_ms": person_ms,
                "face_ms": face_ms,
                "states": resolver.counts(),
                "gallery": len(gallery),
            }

            now = time.time()
            if now - last_metrics >= args.metrics_interval:
                c = stats["states"]
                log.info("REC=%d UNK=%d UNV=%d | events=%d | people=%d faces=%d | "
                         "proc_fps=%.1f person_ms=%.0f face_ms=%.0f",
                         c["RECOGNIZED"], c["UNKNOWN"], c["UNVERIFIED"], engine.count,
                         len(people), len(dets), stats["proc_fps"], person_ms, face_ms)
                last_metrics = now

            draw_people(frame, people, resolver.tracks, dets, stats)
            cv2.putText(frame, f"events: {engine.count}", (10, 68),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(frame, f"events: {engine.count}", (10, 68),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 1, cv2.LINE_AA)

            if args.save:
                if writer is None:
                    writer = cv2.VideoWriter(args.save, cv2.VideoWriter_fourcc(*"mp4v"),
                                             max(proc_rate.fps or 10.0, 5.0),
                                             (frame.shape[1], frame.shape[0]))
                    log.info("Recording to %s", args.save)
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
        stream.stop()
        if writer is not None:
            writer.release()
        if display:
            cv2.destroyAllWindows()
        gallery.close()
        store.close()
        log.info("Done. %d event(s) written to %s", engine.count, cfg.events_db)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
