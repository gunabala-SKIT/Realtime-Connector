"""Runs a `CameraStream` on its own background thread and keeps the latest frame ready to serve.

This is the seam the rest of the plan (`PLAN.md`) is built around: `frame_hooks` is a plain list
of callables invoked on every successfully read frame. Object detection (Phase 2) attaches a
hook here to run YOLO on each frame; nothing in this file has to change when that happens.
Each hook receives `(camera_id, frame, ts)` and must not block for long - it runs on this same
capture thread, so a slow hook would delay the next frame read.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

import cv2

from .config import CameraConfig
from .stream import CameraStream

FrameHook = Callable[[str, "object", float], None]


class CameraRunner:
    def __init__(self, cfg: CameraConfig, logger) -> None:
        self.cfg = cfg
        self.log = logger
        self.stream = CameraStream(cfg, logger)
        self.frame_hooks: list[FrameHook] = []

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._force_reconnect = threading.Event()
        self._frame_lock = threading.Lock()
        self._latest_frame = None
        self._latest_ts = 0.0
        self._latest_jpeg: bytes | None = None
        self._jpeg_at = 0.0
        self.started_ok = False

    # -------------------------------------------------------------------- control
    def start(self) -> bool:
        """Blocking initial connect (bounded retries), then hands the camera to a background
        thread. Returns whether the initial connection succeeded - the caller decides whether
        that should be fatal (a live server keeps retrying either way; a CLI tool may want to exit)."""
        self.started_ok = self.stream.open()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"camera-{self.cfg.camera_id}", daemon=True)
        self._thread.start()
        return self.started_ok

    def stop(self) -> None:
        self._stop.set()
        self.stream.stop()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self.stream.release()

    # ---------------------------------------------------------------------- loop
    def _run(self) -> None:
        while not self._stop.is_set():
            if self._force_reconnect.is_set():
                self._force_reconnect.clear()
                self.stream.release()
            if self.stream.cap is None:
                # start() already tried and failed, or a reconnect gave up permanently:
                # keep trying on a fixed cadence rather than spinning or exiting the thread.
                if not self.stream.connect():
                    self._stop.wait(self.cfg.reconnect_wait_seconds)
                    continue
            ok, frame = self.stream.read_frame()
            if not ok:
                if self.stream.should_reconnect:
                    self.log.warning("Read retry limit reached - reconnecting.")
                    self.stream.reconnect()
                else:
                    self._stop.wait(0.05)
                continue

            ts = time.time()
            with self._frame_lock:
                self._latest_frame = frame
                self._latest_ts = ts
                if ts - self._jpeg_at >= 1.0 / 15:  # re-encode at up to 15 fps regardless of source fps
                    self._latest_jpeg = self._encode(frame)
                    self._jpeg_at = ts

            for hook in self.frame_hooks:
                try:
                    hook(self.cfg.camera_id, frame, ts)
                except Exception:  # noqa: BLE001 - one bad hook must not stop the camera
                    self.log.exception("frame hook failed")

    def _encode(self, frame) -> bytes:
        if self.cfg.stream_max_width and frame.shape[1] > self.cfg.stream_max_width:
            scale = self.cfg.stream_max_width / frame.shape[1]
            frame = cv2.resize(frame, (self.cfg.stream_max_width, int(frame.shape[0] * scale)),
                               interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.cfg.jpeg_quality])
        return buf.tobytes() if ok else b""

    # ------------------------------------------------------------------- reading
    def latest_jpeg(self) -> bytes | None:
        with self._frame_lock:
            return self._latest_jpeg

    def latest_frame(self):
        """The most recent raw frame and the time it was captured, or `(None, 0.0)` before the
        first frame arrives. Detection (Phase 2) can poll this directly instead of using a hook,
        if it prefers to run on its own schedule rather than on every frame."""
        with self._frame_lock:
            return self._latest_frame, self._latest_ts

    def reconnect_now(self) -> None:
        """Ask the capture thread to reconnect on its next loop iteration (used by the API's
        manual-reconnect endpoint, and by tests exercising the disconnect/recover path).

        This only sets an event - it must never call `self.stream.release()` or touch
        `self.stream.cap` directly from this (caller's) thread. `self.stream` is a plain
        `cv2.VideoCapture` wrapper with no thread-safety of its own; only the capture thread in
        `_run()` may call into it. Releasing the camera from a second thread while `_run()` is
        mid-way through `self.cap.read()` was an intermittent crash - found by the API tests,
        which call this endpoint while frames are actively flowing."""
        self._force_reconnect.set()

    def status(self) -> dict:
        s = self.stream.get_status()
        s["camera_id"] = self.cfg.camera_id
        s["camera_name"] = self.cfg.camera_name
        s["running"] = bool(self._thread and self._thread.is_alive())
        return s
