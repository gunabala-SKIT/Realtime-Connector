"""Camera connection settings, all from environment variables (Phase 1, spec section 26).

Nothing here is hard-coded per the project's own rule (spec section 25): the camera source,
credentials, timeouts and reconnect policy all come from `GARMENT_*` environment variables,
loaded from `garment_cv/configs/.env` if present.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from ..config import _get, _get_int, ensure_env_loaded

CRED_RE = re.compile(r"(rtsp://[^:/@\s]+:)[^@\s]*(@)", re.IGNORECASE)


def redact_url(url: str) -> str:
    """``rtsp://user:pass@host`` -> ``rtsp://user:***@host``. Safe to put in logs or the API."""
    return CRED_RE.sub(r"\1***\2", url)


def _get_float(name: str, default: float) -> float:
    try:
        return float(_get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _get_opt_int(name: str) -> int | None:
    v = _get(name)
    if v is None:
        return None
    try:
        return int(v)
    except ValueError:
        return None


@dataclass
class CameraConfig:
    camera_id: str
    camera_name: str
    source: str                      # webcam index ("0"), rtsp://.../udp/http URL, or a local video file path
    camera_user: str
    camera_password: str
    rtsp_transport: str              # "tcp" | "udp"
    open_timeout_ms: int
    read_retry_limit: int
    reconnect_wait_seconds: float
    reconnect_max_attempts: int      # 0 = unlimited
    startup_max_attempts: int
    capture_buffer: int | None
    ffmpeg_loglevel: str | None
    stream_max_width: int            # 0 = no resize before encoding for the live view
    jpeg_quality: int

    @classmethod
    def load(cls, prefix: str = "") -> "CameraConfig":
        """Load one camera. Pass e.g. ``prefix="CAM2_"`` to load a second camera from
        ``CAM2_GARMENT_CAMERA_SOURCE`` etc. without disturbing the first (multi-camera is
        planned per spec section 20, not built yet - this parameter just keeps that door open)."""
        ensure_env_loaded()

        def g(name: str, default: str | None = None) -> str | None:
            return _get(f"{prefix}{name}", default)

        def gi(name: str, default: int) -> int:
            return _get_int(f"{prefix}{name}", default)

        transport = (g("GARMENT_RTSP_TRANSPORT", "tcp") or "tcp").lower()
        if transport not in ("tcp", "udp"):
            transport = "tcp"

        return cls(
            camera_id=g("GARMENT_CAMERA_ID", "camera-1") or "camera-1",
            camera_name=g("GARMENT_CAMERA_NAME", "Camera 1") or "Camera 1",
            source=g("GARMENT_CAMERA_SOURCE", "") or "",
            camera_user=g("GARMENT_CAMERA_USER", "") or "",
            camera_password=g("GARMENT_CAMERA_PASSWORD", "") or "",
            rtsp_transport=transport,
            open_timeout_ms=gi("GARMENT_OPEN_TIMEOUT_MS", 8000),
            read_retry_limit=gi("GARMENT_READ_RETRY_LIMIT", 5),
            reconnect_wait_seconds=_get_float(f"{prefix}GARMENT_RECONNECT_WAIT_SECONDS", 3.0),
            reconnect_max_attempts=gi("GARMENT_RECONNECT_MAX_ATTEMPTS", 0),
            startup_max_attempts=max(1, gi("GARMENT_STARTUP_MAX_ATTEMPTS", 3)),
            capture_buffer=_get_opt_int(f"{prefix}GARMENT_CAPTURE_BUFFER"),
            ffmpeg_loglevel=g("GARMENT_FFMPEG_LOGLEVEL"),
            stream_max_width=gi("GARMENT_STREAM_MAX_WIDTH", 960),
            jpeg_quality=max(10, min(100, gi("GARMENT_JPEG_QUALITY", 80))),
        )

    # ------------------------------------------------------------------ source
    @property
    def is_network_url(self) -> bool:
        return "://" in self.source

    @property
    def is_webcam_index(self) -> bool:
        return self.source.isdigit()

    @property
    def is_file_source(self) -> bool:
        return bool(self.source) and not self.is_webcam_index and not self.is_network_url

    def opencv_source(self):
        """The argument for ``cv2.VideoCapture``: webcam index, file path, or URL (credentials
        merged in if given separately, so they never need to sit in ``GARMENT_CAMERA_SOURCE`` itself)."""
        if self.is_webcam_index:
            return int(self.source)
        if self.is_network_url:
            return self._url_with_credentials()
        return self.source

    def _url_with_credentials(self) -> str:
        scheme, rest = self.source.split("://", 1)
        has_creds = "@" in rest.split("/", 1)[0]
        if has_creds or not (self.camera_user or self.camera_password):
            return self.source
        user, password = quote(self.camera_user, safe=""), quote(self.camera_password, safe="")
        return f"{scheme}://{user}:{password}@{rest}"

    @property
    def safe_source(self) -> str:
        """Credential-masked description of the source - safe for logs, the API and screenshots."""
        if self.is_network_url:
            return redact_url(self._url_with_credentials())
        return self.source

    def ffmpeg_capture_options(self) -> str:
        timeout_us = max(self.open_timeout_ms, 1000) * 1000
        opts = [
            f"rtsp_transport;{self.rtsp_transport}",
            f"timeout;{timeout_us}",
            "max_delay;500000",       # 0.5s reorder window - tolerates network jitter
            "rtsp_flags;prefer_tcp",
        ]
        return "|".join(opts)

    _FFMPEG_LOGLEVELS = {
        "quiet": "-8", "panic": "0", "fatal": "8", "error": "16",
        "warning": "24", "info": "32", "verbose": "40", "debug": "48",
    }

    def opencv_ffmpeg_loglevel(self) -> str | None:
        if not self.ffmpeg_loglevel:
            return None
        v = self.ffmpeg_loglevel.strip().lower()
        if v in self._FFMPEG_LOGLEVELS:
            return self._FFMPEG_LOGLEVELS[v]
        return v if v.lstrip("-").isdigit() else None
