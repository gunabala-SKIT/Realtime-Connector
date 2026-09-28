"""Camera adapter: video -> stage events.

A camera watches one or more stations. Each station has a zone (polygon, normalised 0-1
coordinates). The adapter detects the products in view, tracks them, and turns a product's
presence in a zone into the engine's events:

    product settles in a station's zone   ->  enter   (processing starts)
    product leaves it                     ->  exit    (processing ends; only for process stations)
    at a source station's zone            ->  enter = the product is issued
    at a sink station's zone              ->  enter = the product is finished

Two detectors (`detector` on the camera):

  motion   Background-reference detection. The scene is learned while it is empty (first
           `warmup_frames` frames, or POST .../recalibrate when the line is clear) and then held:
           anything that isn't background is a product - including one that sits still for
           twenty minutes in a machine, which ordinary motion detection would absorb and lose.
           The background only adapts where nothing is detected. Works for any product on a
           fixed camera; parts that touch merge into one blob (use `yolo` if that happens).
  yolo     Ultralytics detection + tracking with the classes/weights given in `params`.

Events are debounced (`confirm_frames`, `exit_hold_s`) with the timestamp of the *first* frame of
the change, so a part flickering at a zone edge is one visit, not several. The exit of a visit
reuses the unit the enter resolved to, so a FIFO guess is never contradicted later. If a QR code
is readable on the product (`decode_qr`), it becomes the unit code.
"""
from __future__ import annotations

import concurrent.futures
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import cv2
import numpy as np

from .engine import ProductionEngine
from .store import Store

log = logging.getLogger("mpi.vision")

DEFAULT_PARAMS = {
    "process_width": 640, "threshold": 30, "min_area": 0.002, "max_area": 0.5, "warmup_frames": 30,
    "confirm_frames": 3, "exit_hold_s": 2.0, "lost_s": 2.0, "qr_wait_s": 2.0,
    "yolo_model": "yolo11n.pt", "yolo_classes": None, "yolo_conf": 0.3,
}
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")


def redact(url: str) -> str:
    return re.sub(r"(://[^:/@\s]+):[^@/\s]+@", r"\1:***@", url or "")


def params_for(cam: dict) -> dict:
    p = dict(DEFAULT_PARAMS)
    p.update({k: v for k, v in (cam.get("params") or {}).items() if k in DEFAULT_PARAMS})
    return p


def point_in_polygon(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


# ------------------------------------------------------------------ detection
@dataclass
class Obj:
    id: int
    box: tuple[float, float, float, float]

    @property
    def centre(self) -> tuple[float, float]:
        return (self.box[0] + self.box[2]) / 2.0, (self.box[1] + self.box[3]) / 2.0


class BackgroundDetector:
    """Frozen-reference background subtraction (see module docstring)."""

    def __init__(self, threshold: int = 30, min_area: float = 0.002, max_area: float = 0.5,
                 warmup_frames: int = 30, alpha: float = 0.002) -> None:
        self.threshold, self.min_area, self.max_area = threshold, min_area, max_area
        self.warmup, self.alpha = warmup_frames, alpha
        self.bg: np.ndarray | None = None
        self.n = 0
        self._k_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        self._k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
        self._k_guard = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21))

    @property
    def ready(self) -> bool:
        return self.bg is not None and self.n >= self.warmup

    def recalibrate(self) -> None:
        self.bg, self.n = None, 0

    def detect(self, frame: np.ndarray) -> list[tuple[float, float, float, float]]:
        # colour, not grey: a green part on a grey belt can have the same brightness as the belt
        img = cv2.GaussianBlur(frame, (5, 5), 0).astype(np.float32)
        if self.bg is None:
            self.bg, self.n = img.copy(), 1
            return []
        if self.n < self.warmup:                       # learn the empty scene (running mean)
            self.n += 1
            self.bg += (img - self.bg) / self.n
            return []
        mask = (cv2.absdiff(img, self.bg).max(axis=2) > self.threshold).astype(np.uint8) * 255
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._k_open)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self._k_close)
        h, w = mask.shape
        total = float(h * w)
        boxes = []
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            area = cv2.contourArea(c)
            if area < self.min_area * total or area > self.max_area * total:
                continue
            x, y, bw, bh = cv2.boundingRect(c)
            boxes.append((float(x), float(y), float(x + bw), float(y + bh)))
        # adapt to slow lighting drift, but never where something is (a parked part must stay a part)
        guard = cv2.dilate(mask, self._k_guard) == 0
        self.bg[guard] += self.alpha * (img[guard] - self.bg[guard])
        return boxes


class CentroidTracker:
    """Greedy nearest-centre tracker that remembers a track for `lost_s` seconds after it vanishes,
    so a part hidden for a moment keeps its id instead of becoming a 'new' part."""

    def __init__(self, lost_s: float = 2.0, gate: float = 1.2) -> None:
        self.lost_s, self.gate = lost_s, gate
        self._next = 1
        self.tracks: dict[int, dict] = {}     # id -> {box, seen}

    def update(self, boxes: list[tuple], ts: float) -> tuple[list[Obj], list[tuple[int, float]]]:
        pairs = []
        for tid, t in self.tracks.items():
            tb = t["box"]
            tc = ((tb[0] + tb[2]) / 2, (tb[1] + tb[3]) / 2)
            size = max(tb[2] - tb[0], tb[3] - tb[1], 1.0)
            for di, b in enumerate(boxes):
                dc = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
                d = ((tc[0] - dc[0]) ** 2 + (tc[1] - dc[1]) ** 2) ** 0.5 / size
                if d <= self.gate:
                    pairs.append((d, tid, di))
        used_t, used_d = set(), set()
        for d, tid, di in sorted(pairs):
            if tid in used_t or di in used_d:
                continue
            used_t.add(tid)
            used_d.add(di)
            self.tracks[tid] = {"box": boxes[di], "seen": ts}
        for di, b in enumerate(boxes):
            if di not in used_d:
                self.tracks[self._next] = {"box": b, "seen": ts}
                used_t.add(self._next)
                self._next += 1
        dropped = [(tid, t["seen"]) for tid, t in self.tracks.items() if tid not in used_t and ts - t["seen"] > self.lost_s]
        for tid, _ in dropped:
            del self.tracks[tid]
        return [Obj(tid, self.tracks[tid]["box"]) for tid in used_t], dropped


class YoloTracker:
    def __init__(self, model: str, conf: float, classes, lost_s: float) -> None:
        from ultralytics import YOLO
        import torch
        self.model = YOLO(model)
        self.conf, self.classes, self.lost_s = conf, classes, lost_s
        self.device = 0 if torch.cuda.is_available() else "cpu"
        self._seen: dict[int, float] = {}

    def recalibrate(self) -> None:  # nothing to learn
        pass

    ready = True

    def update(self, frame: np.ndarray, ts: float) -> tuple[list[Obj], list[tuple[int, float]]]:
        r = self.model.track(frame, persist=True, conf=self.conf, classes=self.classes, device=self.device, verbose=False)[0]
        objs = []
        if r.boxes is not None and r.boxes.id is not None:
            for b, i in zip(r.boxes.xyxy.tolist(), r.boxes.id.int().tolist()):
                objs.append(Obj(int(i), tuple(b)))
                self._seen[int(i)] = ts
        cur = {o.id for o in objs}
        dropped = [(i, s) for i, s in self._seen.items() if i not in cur and ts - s > self.lost_s]
        for i, _ in dropped:
            del self._seen[i]
        return objs, dropped


# ------------------------------------------------------------------ zone logic
@dataclass
class _TrackState:
    in_n: int = 0
    out_n: int = 0
    first_in: float = 0.0
    first_out: float = 0.0
    last_seen: float = 0.0
    confirmed: bool = False
    code: str | None = None          # QR code read on the product, if any
    unit_code: str | None = None     # the unit the enter resolved to
    suppressed: bool = False         # the enter was refused -> stay quiet on exit


class ZoneMonitor:
    """Turns 'track T is inside polygon P' into debounced enter/exit events for one station."""

    def __init__(self, station: dict, poly_px: list[tuple[float, float]], confirm_frames: int, exit_hold_s: float,
                 qr_wait_s: float, decode_qr: bool) -> None:
        self.station, self.poly = station, poly_px
        self.confirm, self.hold, self.qr_wait, self.decode_qr = confirm_frames, exit_hold_s, qr_wait_s, decode_qr
        self.state: dict[int, _TrackState] = {}
        self.inside_now = 0

    def update(self, ts: float, objs: list[Obj], dropped: list[tuple[int, float]],
               decode: Callable[[Obj], str | None]) -> list[tuple[str, float, _TrackState]]:
        out: list[tuple[str, float, _TrackState]] = []
        emit_exit = self.station["kind"] == "process"
        seen = set()
        inside_now = 0
        for o in objs:
            seen.add(o.id)
            st = self.state.get(o.id)
            cx, cy = o.centre
            inside = point_in_polygon(cx, cy, self.poly)
            if st is None:
                if not inside:
                    continue
                st = self.state[o.id] = _TrackState()
            st.last_seen = ts
            if inside:
                inside_now += 1
                st.out_n = 0
                st.in_n += 1
                if st.in_n == 1:
                    st.first_in = ts
                if not st.confirmed and st.in_n >= self.confirm:
                    if self.decode_qr and st.code is None:
                        st.code = decode(o)
                        if st.code is None and ts - st.first_in < self.qr_wait:
                            continue          # give the QR code a moment to become readable
                    st.confirmed = True
                    out.append(("enter", st.first_in, st))
            else:
                if not st.confirmed:
                    del self.state[o.id]      # brushed the edge, never settled
                    continue
                st.out_n += 1
                if st.out_n == 1:
                    st.first_out = ts
                if st.out_n >= self.confirm and ts - st.first_out >= self.hold:
                    if emit_exit:
                        out.append(("exit", st.first_out, st))
                    del self.state[o.id]
        self.inside_now = inside_now
        for tid, last in dropped:               # track vanished (left the frame, or was lost)
            st = self.state.pop(tid, None)
            if st and st.confirmed and emit_exit:
                # it may have left the zone well before it left the picture: use the earlier moment
                out.append(("exit", st.first_out if st.out_n > 0 else last, st))
        for tid in [t for t, s in self.state.items() if t not in seen and not s.confirmed]:
            del self.state[tid]
        return out


# ---------------------------------------------------------------------- runner
class CameraRunner:
    """One camera: capture -> detect/track -> zone events -> engine. `step()` is the per-frame
    logic (also driven directly by tests); `run()` is the capture loop for a thread."""

    def __init__(self, cam: dict, store: Store, engine: ProductionEngine, clock: Callable[[], float] = time.time) -> None:
        self.cam, self.store, self.engine, self.clock = cam, store, engine, clock
        self.p = params_for(cam)
        self.stop_evt = threading.Event()
        self.thread: threading.Thread | None = None
        self.monitors: dict[int, ZoneMonitor] = {}
        self._zones_at = 0.0
        self._qr = cv2.QRCodeDetector() if cam.get("decode_qr") else None
        self.detector = self._make_detector()
        self.tracker = CentroidTracker(self.p["lost_s"]) if cam["detector"] == "motion" else None
        self.size: tuple[int, int] | None = None
        self.latest_jpeg: bytes | None = None
        self.raw_jpeg: bytes | None = None
        self.frames = 0
        self.events_sent = 0
        self.refused = 0
        self.last_event: dict | None = None
        self.error: str | None = None
        self.connected = False
        self.fps = 0.0
        self._fps_t = time.time()
        self._fps_n = 0
        self._draw_t = 0.0
        self.objs: list[Obj] = []
        # Debouncing confirms events late (an exit needs `exit_hold_s` to be sure) and different
        # zones confirm at different delays, so events are held here and released in timestamp order
        # once nothing older can still arrive; otherwise "enter S2" beats "exit S1" to the engine.
        self._pending: list[tuple] = []
        self._seq = 0
        self.flush_delay = self.p["exit_hold_s"] + (self.p["qr_wait_s"] if cam.get("decode_qr") else 0.0) + 1.0

    def _make_detector(self):
        if self.cam["detector"] == "yolo":
            return YoloTracker(self.p["yolo_model"], self.p["yolo_conf"], self.p["yolo_classes"], self.p["lost_s"])
        return BackgroundDetector(self.p["threshold"], self.p["min_area"], self.p["max_area"], self.p["warmup_frames"])

    # -- zones ---------------------------------------------------------------
    def refresh_zones(self, force: bool = False) -> None:
        now = self.clock()
        if not force and now - self._zones_at < 5.0:
            return
        self._zones_at = now
        if self.size is None:
            return
        w, h = self.size
        keep: dict[int, ZoneMonitor] = {}
        for st in self.store.list_stations():
            if st["camera_id"] != self.cam["id"] or not st["zone"]:
                continue
            poly = [(x * w, y * h) for x, y in st["zone"]]
            old = self.monitors.get(st["id"])
            if old and old.poly == poly and old.station["kind"] == st["kind"]:
                keep[st["id"]] = old
            else:
                keep[st["id"]] = ZoneMonitor(st, poly, self.p["confirm_frames"], self.p["exit_hold_s"],
                                             self.p["qr_wait_s"], bool(self._qr))
        self.monitors = keep

    # -- one frame -----------------------------------------------------------
    def step(self, frame: np.ndarray, ts: float, annotate: bool = True) -> list[dict]:
        pw = int(self.p["process_width"])
        if frame.shape[1] > pw:
            frame = cv2.resize(frame, (pw, int(frame.shape[0] * pw / frame.shape[1])), interpolation=cv2.INTER_AREA)
        if self.size != (frame.shape[1], frame.shape[0]):
            self.size = (frame.shape[1], frame.shape[0])
            self.refresh_zones(force=True)
        else:
            self.refresh_zones()
        self.frames += 1
        if self.tracker is not None:
            boxes = self.detector.detect(frame)
            objs, dropped = self.tracker.update(boxes, ts) if self.detector.ready else ([], [])
        else:
            objs, dropped = self.detector.update(frame, ts)
        self.objs = objs
        for sid, mon in self.monitors.items():
            for kind, ets, st in mon.update(ts, objs, dropped, lambda o: self._decode(frame, o)):
                self._seq += 1
                self._pending.append((ets, 0 if kind == "exit" else 1, self._seq, kind, sid, st))
        results = self.flush(ts)
        if annotate and ts - self._draw_t >= 0.08:
            self._draw_t = ts
            self.raw_jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()
            self.latest_jpeg = self._annotate(frame.copy())
        return results

    def flush(self, now: float, everything: bool = False) -> list[dict]:
        """Send held events older than `flush_delay` (or all of them), oldest first; at the same
        instant an exit goes before an enter, so a part handed from one station to the next is
        seen leaving before it arrives."""
        ready = [e for e in self._pending if everything or e[0] <= now - self.flush_delay]
        if not ready:
            return []
        self._pending = [e for e in self._pending if e not in ready]
        return [self._send(kind, sid, ets, st) for ets, _, _, kind, sid, st in sorted(ready, key=lambda e: (e[0], e[1], e[2]))]

    def _decode(self, frame: np.ndarray, o: Obj) -> str | None:
        if self._qr is None:
            return None
        x1, y1, x2, y2 = (int(v) for v in o.box)
        pad = 10
        crop = frame[max(0, y1 - pad):y2 + pad, max(0, x1 - pad):x2 + pad]
        if crop.size == 0:
            return None
        try:
            text, _, _ = self._qr.detectAndDecode(crop)
        except cv2.error:
            return None
        text = (text or "").strip()
        return text[:64].upper() if re.fullmatch(r"[A-Za-z0-9_\-]{3,64}", text) else None

    def _send(self, kind: str, station_id: int, ts: float, st: _TrackState) -> dict:
        if kind == "exit" and st.suppressed:
            return {"result": "skipped"}
        code = st.unit_code if kind == "exit" else st.code
        r = self.engine.record(kind, station_id, ts=ts, unit_code=code, source="camera", camera_id=self.cam["id"])
        if kind == "enter":
            if r["result"] == "applied":
                st.unit_code = r["unit_code"]
            else:
                st.suppressed = True
        self.events_sent += 1
        if r["result"] != "applied":
            self.refused += 1
        self.last_event = {"ts": ts, "type": kind, "station_id": station_id, "result": r["result"], "unit": r.get("unit_code"),
                           "detail": r.get("detail")}
        return r

    def _annotate(self, img: np.ndarray) -> bytes:
        for mon in self.monitors.values():
            pts = np.array(mon.poly, np.int32)
            cv2.polylines(img, [pts], True, (40, 130, 240), 2)
            x, y = int(pts[:, 0].min()), int(pts[:, 1].min())
            cv2.putText(img, f"{mon.station['name']}: {mon.inside_now}", (x, max(y - 6, 14)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (40, 130, 240), 1, cv2.LINE_AA)
        for o in self.objs:
            x1, y1, x2, y2 = (int(v) for v in o.box)
            cv2.rectangle(img, (x1, y1), (x2, y2), (60, 200, 60), 2)
            label = f"#{o.id}"
            for mon in self.monitors.values():
                st = mon.state.get(o.id)
                if st and st.unit_code:
                    label = st.unit_code
            cv2.putText(img, label, (x1, max(y1 - 4, 12)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (60, 200, 60), 1, cv2.LINE_AA)
        if isinstance(self.detector, BackgroundDetector) and not self.detector.ready:
            cv2.putText(img, "learning empty scene...", (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 255), 2, cv2.LINE_AA)
        return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])[1].tobytes()

    def recalibrate(self) -> None:
        self.detector.recalibrate()
        if self.tracker:
            self.tracker.tracks.clear()

    # -- capture loop ----------------------------------------------------------
    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_evt.clear()
        self.thread = threading.Thread(target=self.run, name=f"cam-{self.cam['id']}", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_evt.set()
        if self.thread:
            self.thread.join(timeout=5.0)

    def status(self) -> dict:
        return {"id": self.cam["id"], "name": self.cam["name"], "running": bool(self.thread and self.thread.is_alive()),
                "connected": self.connected, "fps": round(self.fps, 1), "frames": self.frames, "objects": len(self.objs),
                "events_sent": self.events_sent, "events_refused": self.refused, "last_event": self.last_event,
                "error": self.error, "zones": len(self.monitors),
                "learning": isinstance(self.detector, BackgroundDetector) and not self.detector.ready,
                "source": redact(self.cam["source"])}

    def _open(self):
        src = self.cam["source"]
        cap = cv2.VideoCapture(int(src)) if src.isdigit() else cv2.VideoCapture(src)
        return cap if cap.isOpened() else (cap.release() or None)

    def run(self) -> None:
        is_file = os.path.isfile(self.cam["source"])
        backoff = 1.0
        cap = None
        next_frame = time.monotonic()
        while not self.stop_evt.is_set():
            if cap is None:
                cap = self._open()
                if cap is None:
                    self.connected, self.error = False, "cannot open the video source"
                    self.stop_evt.wait(backoff)
                    backoff = min(backoff * 2, 15.0)
                    continue
                backoff, self.connected, self.error = 1.0, True, None
                log.info("camera %s connected (%s)", self.cam["name"], redact(self.cam["source"]))
            if is_file:
                fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
                next_frame += 1.0 / fps
                delay = next_frame - time.monotonic()
                if delay > 0:
                    self.stop_evt.wait(delay)
                else:
                    next_frame = time.monotonic()
            ok, frame = cap.read()
            if not ok and is_file:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)          # loop a demo file
                self.recalibrate()
                ok, frame = cap.read()
            if not ok or frame is None:
                self.connected, self.error = False, "lost the video stream - reconnecting"
                cap.release()
                cap = None
                self.stop_evt.wait(backoff)
                backoff = min(backoff * 2, 15.0)
                continue
            try:
                self.step(frame, self.clock())
            except Exception as e:  # noqa: BLE001 - one bad frame must not kill the camera
                self.error = f"{type(e).__name__}: {e}"
                log.exception("camera %s frame failed", self.cam["name"])
            self._fps_n += 1
            if time.time() - self._fps_t >= 2.0:
                self.fps, self._fps_n, self._fps_t = self._fps_n / (time.time() - self._fps_t), 0, time.time()
        try:
            self.flush(self.clock(), everything=True)
        except Exception:  # noqa: BLE001
            log.exception("camera %s final flush failed", self.cam["name"])
        if cap is not None:
            cap.release()
        self.connected = False


# ---------------------------------------------------------------------- manager
class CameraManager:
    def __init__(self, store: Store, engine: ProductionEngine) -> None:
        self.store, self.engine = store, engine
        self.runners: dict[int, CameraRunner] = {}
        self._lock = threading.Lock()

    def start(self, camera_id: int) -> CameraRunner:
        with self._lock:
            cam = self.store.get_camera(camera_id)
            if not cam:
                raise KeyError(camera_id)
            old = self.runners.get(camera_id)
            if old and old.thread and old.thread.is_alive():
                return old
            r = CameraRunner(cam, self.store, self.engine)
            self.runners[camera_id] = r
        r.start()
        return r

    def stop(self, camera_id: int) -> None:
        with self._lock:
            r = self.runners.pop(camera_id, None)
        if r:
            r.stop()

    def start_enabled(self) -> None:
        for cam in self.store.list_cameras():
            if cam["enabled"]:
                try:
                    self.start(cam["id"])
                except Exception:  # noqa: BLE001
                    log.exception("could not start camera %s", cam["name"])

    def stop_all(self) -> None:
        for cid in list(self.runners):
            self.stop(cid)

    def get(self, camera_id: int) -> CameraRunner | None:
        return self.runners.get(camera_id)

    def status(self, camera_id: int) -> dict:
        r = self.runners.get(camera_id)
        cam = self.store.get_camera(camera_id)
        if r:
            return r.status()
        return {"id": camera_id, "name": cam["name"] if cam else None, "running": False, "connected": False,
                "source": redact(cam["source"]) if cam else None}


def grab_frame(source: str, timeout_s: float = 8.0) -> bytes | None:
    """One JPEG from a source (for 'test connection' and drawing zones on an idle camera). Bounded time -
    opening an unreachable RTSP URL can otherwise block for a long time."""
    def _grab() -> bytes | None:
        cap = cv2.VideoCapture(int(source)) if source.isdigit() else cv2.VideoCapture(source)
        try:
            if not cap.isOpened():
                return None
            for _ in range(5):
                ok, frame = cap.read()
                if ok and frame is not None:
                    return cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()
            return None
        finally:
            cap.release()

    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        return ex.submit(_grab).result(timeout=timeout_s)
    except concurrent.futures.TimeoutError:
        return None
    finally:
        ex.shutdown(wait=False)
