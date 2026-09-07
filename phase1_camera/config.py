"""Configuration loading for Reality Connector Phase 1.

All settings come from environment variables (loaded from ``.env``). Credentials
are never stored anywhere but the :class:`Config` instance, and :pyattr:`Config.safe_source`
returns a credential-masked string that is safe to log.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

_CRED_RE = re.compile(r"(rtsp://[^:/@\s]+:)[^@\s]*(@)", re.IGNORECASE)


def redact_url(url: str) -> str:
    """``rtsp://user:pass@host`` -> ``rtsp://user:***@host``."""
    return _CRED_RE.sub(r"\1***\2", url)


def _get(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _get_int(name: str, default: int) -> int:
    try:
        return int(_get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _get_float(name: str, default: float) -> float:
    try:
        return float(_get(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass
class Config:
    camera_ip: str
    rtsp_port: int
    camera_user: str
    camera_password: str
    camera_channel: str
    rtsp_path_template: str
    rtsp_url_override: str | None
    camera_source: str | None
    rtsp_transport: str
    open_timeout_ms: int
    read_retry_limit: int
    reconnect_wait_seconds: float
    reconnect_max_attempts: int
    startup_max_attempts: int
    target_fps: float
    log_level: str
    log_dir: Path

    @classmethod
    def load(cls, env_file: str | os.PathLike | None = None) -> "Config":
        load_dotenv(env_file or (BASE_DIR / ".env"))

        log_dir = Path(_get("LOG_DIR", "logs"))
        if not log_dir.is_absolute():
            log_dir = BASE_DIR / log_dir

        transport = (_get("RTSP_TRANSPORT", "tcp") or "tcp").lower()
        if transport not in ("tcp", "udp"):
            transport = "tcp"

        return cls(
            camera_ip=_get("CAMERA_IP", "") or "",
            rtsp_port=_get_int("RTSP_PORT", 554),
            camera_user=_get("CAMERA_USER", "") or "",
            camera_password=_get("CAMERA_PASSWORD", "") or "",
            camera_channel=_get("CAMERA_CHANNEL", "101") or "101",
            rtsp_path_template=_get("CAMERA_RTSP_PATH", "/Streaming/Channels/{channel}"),
            rtsp_url_override=_get("RTSP_URL"),
            camera_source=_get("CAMERA_SOURCE"),
            rtsp_transport=transport,
            open_timeout_ms=_get_int("OPEN_TIMEOUT_MS", 8000),
            read_retry_limit=_get_int("READ_RETRY_LIMIT", 5),
            reconnect_wait_seconds=_get_float("RECONNECT_WAIT_SECONDS", 3.0),
            reconnect_max_attempts=_get_int("RECONNECT_MAX_ATTEMPTS", 0),
            startup_max_attempts=max(1, _get_int("STARTUP_MAX_ATTEMPTS", 3)),
            target_fps=_get_float("TARGET_FPS", 0.0),
            log_level=(_get("LOG_LEVEL", "INFO") or "INFO").upper(),
            log_dir=log_dir,
        )

    # ------------------------------------------------------------------ source
    @property
    def uses_rtsp(self) -> bool:
        return self.camera_source is None

    def opencv_source(self):
        """The argument for ``cv2.VideoCapture``: webcam index, file path, or RTSP URL."""
        if self.camera_source is not None:
            return int(self.camera_source) if self.camera_source.isdigit() else self.camera_source
        return self._rtsp_url()

    def _rtsp_url(self) -> str:
        user = quote(self.camera_user, safe="")
        password = quote(self.camera_password, safe="")

        if self.rtsp_url_override:
            after_scheme = self.rtsp_url_override.split("://", 1)[-1]
            if "@" in after_scheme.split("/", 1)[0]:
                return self.rtsp_url_override  # already has credentials
            scheme, rest = self.rtsp_url_override.split("://", 1)
            return f"{scheme}://{user}:{password}@{rest}"

        path = self.rtsp_path_template.format(channel=self.camera_channel)
        if not path.startswith("/"):
            path = "/" + path
        return f"rtsp://{user}:{password}@{self.camera_ip}:{self.rtsp_port}{path}"

    @property
    def safe_source(self) -> str:
        """Credential-masked description of the source - safe for logs and screenshots."""
        if self.camera_source is not None:
            return f"local:{self.camera_source}"
        return redact_url(self._rtsp_url())

    def ffmpeg_capture_options(self) -> str:
        # socket I/O timeout in microseconds. FFmpeg >= 5 uses "timeout"; older builds
        # used "stimeout". OpenCV's bundled FFmpeg is recent, so "timeout" is correct here.
        timeout_us = max(self.open_timeout_ms, 1000) * 1000
        return f"rtsp_transport;{self.rtsp_transport}|timeout;{timeout_us}"
