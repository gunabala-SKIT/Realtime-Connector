"""Reusable camera ingestion component (Phase 1 doc section 14).

``CameraStream`` owns *only* stream connection, decoding, validation, reconnect
and metrics. It contains no AI logic, so Phase 2 (YOLO / tracking / face
recognition) can consume frames from it without change.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Optional, Tuple

import cv2

from .config import Config


@dataclass
class Metrics:
    frames_received: int = 0
    frames_failed: int = 0
    connection_attempts: int = 0
    reconnections: int = 0
    connected_since: Optional[float] = None
    last_frame_time: Optional[float] = None
    _fps_samples: list = field(default_factory=list)

    def note_frame(self) -> None:
        now = time.time()
        self.frames_received += 1
        if self.last_frame_time is not None:
            dt = now - self.last_frame_time
            if dt > 0:
                self._fps_samples.append(dt)
                if len(self._fps_samples) > 90:
                    self._fps_samples.pop(0)
        self.last_frame_time = now

    @property
    def actual_fps(self) -> float:
        if not self._fps_samples:
            return 0.0
        return round(len(self._fps_samples) / sum(self._fps_samples), 2)

    @property
    def stream_uptime(self) -> float:
        return round(time.time() - self.connected_since, 1) if self.connected_since else 0.0


_TIMEOUT_PROPS = [
    p for p in ("CAP_PROP_OPEN_TIMEOUT_MSEC", "CAP_PROP_READ_TIMEOUT_MSEC") if hasattr(cv2, p)
]


class CameraStream:
    def __init__(self, config: Config, logger) -> None:
        self.cfg = config
        self.log = logger
        self.cap: Optional[cv2.VideoCapture] = None
        self.metrics = Metrics()
        self._width = 0
        self._height = 0
        self._reported_fps = 0.0
        self._consecutive_failures = 0
        self._stopped = False
        self._next_file_frame = 0.0

    # ---------------------------------------------------------------- lifecycle
    def connect(self) -> bool:
        if self.cfg.uses_rtsp:
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = self.cfg.ffmpeg_capture_options()
            level = self.cfg.opencv_ffmpeg_loglevel()
            if level is not None:
                os.environ["OPENCV_FFMPEG_LOGLEVEL"] = level

        self.metrics.connection_attempts += 1
        source = self.cfg.opencv_source()
        self.log.info("Connecting to camera source: %s", self.cfg.safe_source)

        if self.cfg.uses_rtsp:
            params = []
            for prop_name in _TIMEOUT_PROPS:  # applied *before* open, unlike a post-hoc .set()
                params += [getattr(cv2, prop_name), self.cfg.open_timeout_ms]
            cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG, params)
        else:
            cap = cv2.VideoCapture(source)
        if self.cfg.capture_buffer is not None:
            # Only set when asked: forcing a 1-frame buffer lowers latency but makes
            # a lossy RTSP stream decode worse (dropped frames break the reference chain).
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, self.cfg.capture_buffer)
            except cv2.error:
                pass

        if not cap.isOpened():
            cap.release()
            self.cap = None
            self.log.error(
                "Unable to open stream (isOpened() == False). "
                "Check: network reachability, RTSP port, credentials, channel id, codec."
            )
            return False

        self._reported_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)

        # Warm-up: pull frames for a few seconds until one decodes. Many RTSP streams
        # report 0x0 until the first keyframe arrives. If nothing decodes, the stream
        # opened but has no usable video (bad codec / missing SPS-PPS / dead channel).
        warmup_deadline = time.time() + max(self.cfg.open_timeout_ms / 1000.0, 5.0)
        first = None
        while time.time() < warmup_deadline and not self._stopped:
            ok, frame = cap.read()
            if ok and frame is not None and self._valid_frame(frame):
                first = frame
                break
            time.sleep(0.02)
        if first is None:
            cap.release()
            self.cap = None
            self.log.error(
                "Stream opened but delivered no decodable video within warm-up. "
                "Likely a broken bitstream from the DVR (missing SPS/PPS, corrupt keyframes) "
                "or an empty channel. Try: set the DVR stream to H.264, shorten the I-frame "
                "interval, reboot the DVR, or verify the channel has a live camera."
            )
            return False

        self.cap = cap
        self._height, self._width = first.shape[:2]
        self.metrics.connected_since = time.time()
        self.metrics.last_frame_time = None
        self._consecutive_failures = 0
        self.metrics.note_frame()
        self.log.info(
            "Stream opened. resolution=%dx%d reported_fps=%.1f transport=%s",
            self._width,
            self._height,
            self._reported_fps,
            self.cfg.rtsp_transport if self.cfg.uses_rtsp else "n/a",
        )
        return True

    def open(self) -> bool:
        """Initial connection with bounded retries so the app never hangs on a dead DVR."""
        attempts = self.cfg.startup_max_attempts
        for attempt in range(1, attempts + 1):
            if self._stopped:
                return False
            if self.connect():
                return True
            if attempt < attempts:
                self.log.warning(
                    "Initial connect %d/%d failed; retrying in %.1f s.",
                    attempt, attempts, self.cfg.reconnect_wait_seconds,
                )
                time.sleep(self.cfg.reconnect_wait_seconds)
        self.log.error("Initial connection failed after %d attempt(s).", attempts)
        return False

    def _is_file_source(self) -> bool:
        src = self.cfg.camera_source
        return bool(src) and not str(src).isdigit() and "://" not in str(src)

    def _pace_file(self) -> None:
        """Play a video file at its recorded frame rate, like a camera would. Without
        this it's decoded as fast as the disk allows - a minute-long demo clip
        flashes past in seconds and loops, and timed checks see random frames."""
        interval = 1.0 / (self._reported_fps if 0 < self._reported_fps < 240 else 25.0)
        now = time.monotonic()
        if now < self._next_file_frame:
            time.sleep(self._next_file_frame - now)
        self._next_file_frame = max(self._next_file_frame + interval, time.monotonic())

    def read_frame(self) -> Tuple[bool, Optional["cv2.Mat"]]:
        """One read attempt. Returns ``(False, None)`` on any failure; never raises."""
        if self.cap is None:
            return False, None

        if self._is_file_source():
            self._pace_file()
        ok, frame = self.cap.read()
        if (not ok or frame is None) and self._is_file_source():
            # a video file just hit EOF - loop it (handy for testing/tuning)
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self.cap.read()

        if not ok or frame is None or not self._valid_frame(frame):
            self.metrics.frames_failed += 1
            self._consecutive_failures += 1
            self.log.warning(
                "Frame read failed (%d consecutive, %d total).",
                self._consecutive_failures,
                self.metrics.frames_failed,
            )
            return False, None

        self._consecutive_failures = 0
        self.metrics.note_frame()
        return True, frame

    @staticmethod
    def _valid_frame(frame) -> bool:
        return (
            getattr(frame, "size", 0) > 0
            and getattr(frame, "ndim", 0) == 3
            and frame.shape[0] > 0
            and frame.shape[1] > 0
        )

    @property
    def should_reconnect(self) -> bool:
        return self._consecutive_failures >= self.cfg.read_retry_limit

    def is_connected(self) -> bool:
        return (
            self.cap is not None
            and self.cap.isOpened()
            and self._consecutive_failures < self.cfg.read_retry_limit
        )

    def reconnect(self) -> bool:
        """Release and reconnect per policy. Blocks until success or attempts exhausted."""
        self.metrics.reconnections += 1
        self.release()
        attempt = 0
        while not self._stopped:
            attempt += 1
            self.log.warning("Reconnect attempt %d ...", attempt)
            if self.connect():
                self.log.info("Reconnected after %d attempt(s).", attempt)
                return True
            if self.cfg.reconnect_max_attempts and attempt >= self.cfg.reconnect_max_attempts:
                self.log.error("Giving up after %d reconnect attempt(s).", attempt)
                return False
            time.sleep(self.cfg.reconnect_wait_seconds)
        return False

    def release(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        self.metrics.connected_since = None

    def stop(self) -> None:
        """Break out of any in-progress reconnect loop (called from a signal handler)."""
        self._stopped = True

    # ------------------------------------------------------------- introspection
    def get_fps(self) -> float:
        return self.metrics.actual_fps

    def get_resolution(self) -> Tuple[int, int]:
        return self._width, self._height

    def get_status(self) -> dict:
        return {
            "connected": self.is_connected(),
            "source": self.cfg.safe_source,
            "resolution": f"{self._width}x{self._height}",
            "reported_fps": self._reported_fps,
            "actual_fps": self.metrics.actual_fps,
            "frames_received": self.metrics.frames_received,
            "frames_failed": self.metrics.frames_failed,
            "connection_attempts": self.metrics.connection_attempts,
            "reconnections": self.metrics.reconnections,
            "stream_uptime_s": self.metrics.stream_uptime,
            "consecutive_failures": self._consecutive_failures,
        }
