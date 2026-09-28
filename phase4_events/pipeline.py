"""The Phase 4 vision pipeline as a reusable, threadable component.

Both the CLI viewer (`phase4_events/app.py`) and the dashboard
(`phase5_dashboard/server.py`) drive this. It runs the loop in a background
thread, writes deduplicated events, and always exposes the newest annotated
frame (raw ndarray + a JPEG) for display or MJPEG streaming.
"""
from __future__ import annotations

import threading
import time
from collections import deque

import cv2

from phase1_camera.camera import CameraStream
from phase2_detect.detector import PersonTracker
from phase2_detect.stream import LatestFrameStream
from phase3_identity.draw import draw_people
from phase3_identity.faces import FaceEngine
from phase3_identity.gallery import EmployeeGallery
from phase3_identity.identity import IdentityResolver

from .config import Phase4Config
from .engine import EventEngine
from .store import EventStore


def _face_in_person(face, person) -> bool:
    return person.x1 <= face.cx <= person.x2 and person.y1 <= face.cy <= person.y2


class _Rate:
    def __init__(self, n: int = 60) -> None:
        self.t: deque = deque(maxlen=n)

    def tick(self) -> None:
        self.t.append(time.perf_counter())

    @property
    def fps(self) -> float:
        if len(self.t) < 2:
            return 0.0
        dt = self.t[-1] - self.t[0]
        return (len(self.t) - 1) / dt if dt > 0 else 0.0


class VisionPipeline:
    def __init__(self, cfg: Phase4Config, logger) -> None:
        self.cfg = cfg
        self.log = logger
        p3 = cfg.phase3
        self.stream = LatestFrameStream(CameraStream(p3.phase2.camera, logger), logger)
        self.tracker = PersonTracker(p3.phase2, logger)
        self.faces = FaceEngine(p3, logger)
        self.gallery = EmployeeGallery(p3.gallery_db)
        self.resolver = IdentityResolver(p3, self.gallery, logger)
        self.store = EventStore(cfg.events_db)
        self.engine = EventEngine(cfg, self.store, logger)

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._frame = None          # latest annotated ndarray
        self._jpeg: bytes | None = None
        self._rate = _Rate()
        self.person_ms = self.face_ms = 0.0
        self.started_at = 0.0

        # Called with the list of newly-written Events after each frame (Phase 6
        # wires the rule engine in here without Phase 4 needing to know about it).
        self.event_hooks: list = []

        # Called every frame with (camera_id, positions, frame_w, frame_h) -
        # ALL currently-live track boxes, not just ones that just raised an
        # event. Phase 9 wires queue/crowd counting in here: occupancy needs
        # every live track on a schedule of its own, not just identity
        # transitions the way zone-tagging (Phase 7) piggybacks on events.
        self.frame_hooks: list = []

        # Called every frame with (camera_id, frame, positions, identity_tracks)
        # for hooks that need the pixels (Phase 14 PPE runs its own model on
        # them). `frame` is the raw camera image - boxes get drawn onto it right
        # after the hooks return, so a hook that keeps it must copy it.
        self.image_hooks: list = []

    # --------------------------------------------------------------- control
    def start(self) -> None:
        self.log.info("Warming up models ...")
        self.tracker.warmup()
        self.faces.warmup()
        self._sync_employees()
        self.log.info("camera_id=%s gallery=%d events_db=%s",
                      self.cfg.camera_id, len(self.gallery), self.cfg.events_db)
        if not self.stream.start():
            raise RuntimeError("could not connect to the camera source")
        self.started_at = time.time()
        self._thread = threading.Thread(target=self._loop, name="pipeline", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        self.stream.stop()
        self.store.close()
        self.gallery.close()

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _sync_employees(self) -> None:
        self.store.sync_employees([
            {"code": e.code, "name": e.name, "department": e.department,
             "status": e.status, "n_images": e.n_images}
            for e in self.gallery.list()
        ])

    # ----------------------------------------------------------------- loop
    def _loop(self) -> None:
        last_seq = 0
        while not self._stop.is_set():
            frame, seq = self.stream.read_latest(last_seq)
            if frame is None:
                if not (self.stream._thread and self.stream._thread.is_alive()):
                    self.log.error("Capture thread stopped - pipeline ending.")
                    return
                time.sleep(0.004)
                continue
            last_seq = seq
            self.resolver.begin_frame()

            t0 = time.perf_counter()
            people = [t for t in self.tracker.process(frame) if t.cls == 0]
            self.person_ms = (time.perf_counter() - t0) * 1000

            t1 = time.perf_counter()
            dets = self.faces.detect(frame)
            self.face_ms = (time.perf_counter() - t1) * 1000

            live_ids: set[int] = set()
            for person in people:
                if person.track_id is None:
                    continue
                live_ids.add(person.track_id)
                q_face = None
                if self.resolver.should_recognize(person.track_id):
                    inside = [f for f in dets if _face_in_person(f, person)]
                    if len(inside) == 1 and self.faces.quality(inside[0]).passed:
                        q_face = inside[0]
                self.resolver.update(person.track_id, q_face)

            self.resolver.prune(live_ids)
            positions = {p.track_id: (p.x1, p.y1, p.x2, p.y2) for p in people if p.track_id is not None}
            new_events = self.engine.observe(self.resolver.tracks, live_ids, frame, positions)
            for hook in self.event_hooks:
                try:
                    hook(new_events)
                except Exception:  # noqa: BLE001 - a hook must never break the vision loop
                    self.log.exception("event hook failed")
            if self.frame_hooks:
                fh, fw = frame.shape[:2]
                for hook in self.frame_hooks:
                    try:
                        hook(self.cfg.camera_id, positions, fw, fh)
                    except Exception:  # noqa: BLE001 - a hook must never break the vision loop
                        self.log.exception("frame hook failed")
            for hook in self.image_hooks:
                try:
                    hook(self.cfg.camera_id, frame, positions, self.resolver.tracks)
                except Exception:  # noqa: BLE001 - a hook must never break the vision loop
                    self.log.exception("image hook failed")
            self._rate.tick()

            draw_people(frame, people, self.resolver.tracks, dets, self.status())
            cv2.putText(frame, f"events: {self.engine.count}", (10, 68),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(frame, f"events: {self.engine.count}", (10, 68),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 1, cv2.LINE_AA)

            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
            with self._lock:
                self._frame = frame
                if ok:
                    self._jpeg = buf.tobytes()

    # --------------------------------------------------------------- outputs
    def latest_frame(self):
        with self._lock:
            return self._frame

    def latest_jpeg(self) -> bytes | None:
        with self._lock:
            return self._jpeg

    def status(self) -> dict:
        f = self._frame
        h, w = (f.shape[0], f.shape[1]) if f is not None else (0, 0)
        return {
            "resolution": f"{w}x{h}",
            "proc_fps": round(self._rate.fps, 1),
            "person_ms": round(self.person_ms),
            "face_ms": round(self.face_ms),
            "states": self.resolver.counts(),
            "gallery": len(self.gallery),
            "events": self.engine.count,
            "camera_id": self.cfg.camera_id,
            "uptime_s": round(time.time() - self.started_at) if self.started_at else 0,
            "alive": self.alive,
        }

    def mjpeg(self, fps_cap: float = 15.0):
        """multipart/x-mixed-replace generator for <img src>."""
        interval = 1.0 / fps_cap
        while not self._stop.is_set():
            with self._lock:
                jpg = self._jpeg
            if jpg is None:
                time.sleep(0.1)
                continue
            yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                   + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
            time.sleep(interval)
