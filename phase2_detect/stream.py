"""Latest-frame capture: a background thread keeps only the newest frame.

Without this, a slow inference pass lets frames queue in the decoder and latency
grows without bound (you end up watching old video). With it, inference always
runs on the freshest frame and stale ones are simply overwritten - the app trades
frame drops for bounded latency, which is what a live monitor wants.

This is the depth-1 frame queue between the "ingestion" and "AI worker" tiers in
docs/architecture.html.
"""
from __future__ import annotations

import threading
import time

from phase1_camera.camera import CameraStream


class LatestFrameStream:
    def __init__(self, cam: CameraStream, logger) -> None:
        self.cam = cam
        self.log = logger
        self._frame = None
        self._seq = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.capture_fps = 0.0

    def start(self) -> bool:
        if not self.cam.open():
            return False
        self._thread = threading.Thread(target=self._loop, name="capture", daemon=True)
        self._thread.start()
        return True

    def _loop(self) -> None:
        ticks: list[float] = []
        while not self._stop.is_set():
            ok, frame = self.cam.read_frame()
            if not ok:
                if self.cam.should_reconnect:
                    self.log.warning("capture: read retry limit - reconnecting.")
                    if not self.cam.reconnect():
                        self.log.error("capture: reconnect failed permanently - capture stopped.")
                        return
                else:
                    time.sleep(0.01)
                continue
            with self._lock:
                self._frame = frame
                self._seq += 1
            now = time.perf_counter()
            ticks.append(now)
            if len(ticks) > 60:
                ticks.pop(0)
            if len(ticks) > 1:
                self.capture_fps = (len(ticks) - 1) / (ticks[-1] - ticks[0])

    def read_latest(self, since_seq: int):
        """Return (frame, seq) if a newer frame exists, else (None, since_seq)."""
        with self._lock:
            if self._frame is None or self._seq == since_seq:
                return None, since_seq
            return self._frame, self._seq

    def stop(self) -> None:
        self._stop.set()
        self.cam.stop()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self.cam.release()

    def get_status(self) -> dict:
        s = self.cam.get_status()
        s["capture_fps"] = round(self.capture_fps, 1)
        return s
