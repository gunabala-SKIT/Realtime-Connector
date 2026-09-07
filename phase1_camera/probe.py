"""Probe common Hikvision / ONVIF RTSP URL patterns to find the one your DVR uses.

Reads CAMERA_IP / RTSP_PORT / CAMERA_USER / CAMERA_PASSWORD from .env, then tries
a list of candidate paths with a short timeout and reports which ones open and
deliver a frame.

    python -m phase1_camera.probe

When one works, set it in .env as:
    CAMERA_RTSP_PATH=<the working path>      (CAMERA_CHANNEL is then ignored)
or use the full override:
    RTSP_URL=rtsp://<ip>:<port><the working path>
"""
from __future__ import annotations

import os
import sys
from urllib.parse import quote

import cv2

from .config import Config, redact_url

# Candidate paths. {c} is substituted with the camera/channel number (1-based).
CANDIDATES = [
    "/Streaming/Channels/{c}01",          # Hikvision ISAPI, main stream (101, 201, ...)
    "/Streaming/Channels/{c}02",          # Hikvision ISAPI, sub stream  (102, 202, ...)
    "/Streaming/Channels/{c}",            # some firmware: bare channel number
    "/Streaming/Unicast/channels/{c}01",  # Hikvision alternate
    "/ISAPI/Streaming/channels/{c}01",
    "/h264/ch{c}/main/av_stream",         # older Hikvision
    "/h264/ch{c}/sub/av_stream",
    "/mpeg4/ch{c}/main/av_stream",
    "/cam/realmonitor?channel={c}&subtype=0",   # ONVIF-ish / Dahua-style
    "/cam/realmonitor?channel={c}&subtype=1",
    "/live/ch{cz}",                       # ch0-indexed (ch0, ch1, ...)
    "/live/ch{cz}_0",
]

CHANNELS = [1, 2]          # try camera 1 and 2
OPEN_TIMEOUT_MS = 4000     # keep each attempt short


def try_url(url: str) -> tuple[bool, str]:
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
        f"rtsp_transport;tcp|timeout;{OPEN_TIMEOUT_MS * 1000}"
    )
    params = []
    for prop in ("CAP_PROP_OPEN_TIMEOUT_MSEC", "CAP_PROP_READ_TIMEOUT_MSEC"):
        if hasattr(cv2, prop):
            params += [getattr(cv2, prop), OPEN_TIMEOUT_MS]
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG, params)
    try:
        if not cap.isOpened():
            return False, "not opened"
        ok, frame = cap.read()
        if not ok or frame is None:
            return False, "opened but no frame"
        h, w = frame.shape[:2]
        return True, f"OK  {w}x{h}"
    finally:
        cap.release()


def main() -> int:
    cfg = Config.load()
    if not cfg.camera_ip:
        print("CAMERA_IP is not set. Fill in phase1_camera/.env first.")
        return 2

    user = quote(cfg.camera_user, safe="")
    pw = quote(cfg.camera_password, safe="")
    base = f"rtsp://{user}:{pw}@{cfg.camera_ip}:{cfg.rtsp_port}"

    print(f"Probing {cfg.camera_ip}:{cfg.rtsp_port} as user '{cfg.camera_user}'")
    print(f"Each attempt waits up to {OPEN_TIMEOUT_MS / 1000:.0f}s.\n")

    winners = []
    for tmpl in CANDIDATES:
        for c in CHANNELS:
            path = tmpl.format(c=c, cz=c - 1)
            url = base + path
            ok, detail = try_url(url)
            mark = "  ✓" if ok else "   "
            print(f"{mark} {path:<42} {detail}")
            if ok:
                winners.append(path)
        if "{c}" not in tmpl and "{cz}" not in tmpl:
            break  # path had no channel token; one try was enough

    print()
    if winners:
        best = winners[0]
        print(f"Working path: {best}")
        print("Set this in phase1_camera/.env:")
        print(f"    CAMERA_RTSP_PATH={best}")
        print("(then CAMERA_CHANNEL is ignored). Re-run:  python -m phase1_camera.app")
        return 0

    print("No candidate path opened a stream. Likely causes:")
    print("  - wrong username / password (try the account you use in the DVR UI)")
    print("  - RTSP disabled on the DVR, or RTSP authentication set to a mode OpenCV can't use")
    print("  - this channel has no camera attached")
    print(f"  - a non-standard path — check the DVR manual, then set RTSP_URL in .env")
    print(f"\nManual test in VLC:  {redact_url(base)}/Streaming/Channels/101")
    return 1


if __name__ == "__main__":
    sys.exit(main())
