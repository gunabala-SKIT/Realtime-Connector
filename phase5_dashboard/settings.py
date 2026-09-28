"""Settings page backend: read and edit ``phase1_camera/.env`` from the dashboard.

Every phase loads that one file at process start, so it stays the single source
of truth - this module just edits it in place. Saved changes apply on the next
restart of whichever process uses them; each field reports whether the saved
value differs from what the running process started with.

Only keys in :data:`SECTIONS` are writable. Secrets are never returned, and
credentials embedded in camera URLs are shown redacted.
"""
from __future__ import annotations

import asyncio
import base64
import dataclasses
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import cv2
from dotenv import dotenv_values
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from phase1_camera.config import Config

_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
_EMAIL_RE = re.compile(r"^[^@\s,]+@[^@\s,]+$")
_URL_CREDS_RE = re.compile(r"^([a-z][a-z0-9+.-]*://)([^:/@\s]+):([^@\s]*)@", re.IGNORECASE)
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_MARKER = "# --- added by the dashboard Settings page ---"


# --------------------------------------------------------------------- catalog
@dataclass(frozen=True)
class Field:
    key: str
    label: str
    type: str  # text | secret | int | number | select | bool | camera_url
    help: str = ""
    default: str = ""
    placeholder: str = ""
    options: tuple[str, ...] = ()
    min: float | None = None
    max: float | None = None
    step: float | None = None
    required: bool = False
    check: Callable[[str], str | None] | None = None  # returns an error message


@dataclass(frozen=True)
class Section:
    id: str
    title: str
    description: str
    process: str  # dashboard | shelf | vehicle - which process reads these keys
    fields: tuple[Field, ...]
    camera_prefix: str | None = None


def _check_id(v: str) -> str | None:
    return None if _ID_RE.match(v) else "Use 1-40 letters, digits, dots, dashes or underscores."


def _check_email(v: str) -> str | None:
    return None if not v or _EMAIL_RE.match(v) else "Enter a valid email address."


def _check_email_list(v: str) -> str | None:
    bad = [a for a in (p.strip() for p in v.split(",")) if a and not _EMAIL_RE.match(a)]
    return f"Not a valid email address: {bad[0]}" if bad else None


def _check_ppe_items(v: str) -> str | None:
    from phase4_events.store import PPE_ITEMS
    items = [p.strip().lower() for p in v.split(",") if p.strip()]
    bad = [i for i in items if i not in PPE_ITEMS]
    if bad:
        return f"Unknown item: {bad[0]}. Use: {', '.join(PPE_ITEMS)}."
    return None if items else "List at least one item."


def _camera_fields(prefix: str, default_id: str, required: bool) -> tuple[Field, ...]:
    return (
        Field(f"{prefix}CAMERA_ID", "Camera ID", "text", default=default_id, required=True,
              help="Label stored on every event from this camera; also used for entry/exit roles.",
              check=_check_id),
        Field(f"{prefix}CAMERA_URL", "Stream source", "camera_url", required=required,
              placeholder="rtsp://192.168.1.64:554/Streaming/Channels/101",
              help="rtsp://... for IP cameras and DVRs, http://.../video for MJPEG phone apps, "
                   "0 for a USB webcam, or a video file path for demos."),
        Field(f"{prefix}CAMERA_USER", "Username", "text",
              help="Added to rtsp:// URLs that don't already include credentials."),
        Field(f"{prefix}CAMERA_PASSWORD", "Password", "secret"),
        Field(f"{prefix}RTSP_TRANSPORT", "RTSP transport", "select", options=("tcp", "udp"),
              default="tcp", help="TCP is reliable; UDP has lower latency but drops frames on weak Wi-Fi."),
    )


SECTIONS: tuple[Section, ...] = (
    Section("camera", "Main camera", "The camera this dashboard watches.", "dashboard",
            _camera_fields("", "cam-1", required=True), camera_prefix=""),
    Section("detection", "Person detection", "YOLO person detector and tracker.", "dashboard", (
        Field("YOLO_MODEL", "Model", "select", default="yolo11n.pt",
              options=("yolo11n.pt", "yolo11s.pt", "yolo11m.pt"),
              help="n is fastest, s is balanced, m catches the most people but runs slowest."),
        Field("YOLO_CONF", "Confidence threshold", "number", default="0.35", min=0.05, max=0.95, step=0.05,
              help="Lower catches smaller or partly hidden people, with more false detections."),
        Field("YOLO_IMGSZ", "Inference size (px)", "select", default="640", options=("640", "960", "1280"),
              help="Larger finds distant people but costs speed."),
        Field("PROCESS_EVERY_N", "Process every Nth frame", "int", default="1", min=1, max=10,
              help="Raise if the processing FPS can't keep up with the camera."),
    )),
    Section("identity", "Face recognition", "Matching faces against enrolled employees.", "dashboard", (
        Field("RECOGNITION_THRESHOLD", "Match threshold", "number", default="0.35", min=0.1, max=0.9, step=0.01,
              help="Higher is stricter: fewer false matches, more people left as UNKNOWN."),
        Field("IDENTITY_CONFIRM_FRAMES", "Confirm frames", "int", default="3", min=1, max=20,
              help="Frames that must agree before a person is marked RECOGNIZED or UNKNOWN."),
        Field("FACE_MIN_IOD", "Minimum face size (px)", "number", default="40", min=10, max=200, step=1,
              help="Distance between the eyes; faces smaller than this are ignored."),
    )),
    Section("events", "Events", "When sightings become events.", "dashboard", (
        Field("EMIT_UNVERIFIED_EVENTS", "Log every unidentified person", "bool", default="false",
              help="Off: unidentified people are logged only inside a zone. On: logged everywhere (noisy)."),
        Field("DEDUP_TRACK_TTL_S", "Track memory (seconds)", "number", default="8", min=1, max=120, step=1,
              help="How long a person who leaves the frame is remembered before counting as new."),
    )),
    Section("email", "Email notifications", "Sends alert emails for rules with email enabled. Leave the host "
            "blank to turn email off.", "dashboard", (
        Field("SMTP_HOST", "SMTP host", "text", placeholder="smtp.gmail.com"),
        Field("SMTP_PORT", "Port", "int", default="587", min=1, max=65535),
        Field("SMTP_USE_TLS", "Use TLS", "bool", default="true"),
        Field("SMTP_USER", "Username", "text", placeholder="you@example.com"),
        Field("SMTP_PASSWORD", "Password", "secret", help="For Gmail, use an app password."),
        Field("SMTP_FROM", "From address", "text", placeholder="you@example.com", check=_check_email),
        Field("ALERT_EMAIL_TO", "Send alerts to", "text", placeholder="security@example.com, manager@example.com",
              help="Comma-separated.", check=_check_email_list),
    )),
    Section("counting", "Queues & footfall", "Crowd-zone checks and line-crossing counts.", "dashboard", (
        Field("QUEUE_CHECK_INTERVAL_S", "Queue check interval (s)", "number", default="5", min=1, max=60, step=1),
        Field("QUEUE_ZONE_MARGIN", "Zone edge tolerance", "number", default="0.08", min=0, max=0.3, step=0.01,
              help="Grows zones slightly so people standing on the edge still count."),
        Field("FOOTFALL_CONFIRM_FRAMES", "Crossing confirm frames", "int", default="4", min=1, max=20),
        Field("FOOTFALL_RECROSS_COOLDOWN_S", "Re-cross cooldown (s)", "number", default="2", min=0, max=30,
              step=0.5, help="Stops someone swaying on the line from being counted repeatedly."),
    )),
    Section("ppe", "Safety (PPE)", "Checks people in PPE zones for helmets, vests and other equipment.",
            "dashboard", (
        Field("PPE_REQUIRED_ITEMS", "Default items for new PPE zones", "text", default="helmet,vest,gloves",
              placeholder="helmet,vest,gloves", check=_check_ppe_items,
              help="Pre-ticked when you mark a zone as a PPE zone. Any of: helmet, vest, gloves, boots, goggles."),
        Field("PPE_CONF", "Detection confidence", "number", default="0.4", min=0.05, max=0.95, step=0.05,
              help="Lower catches more equipment but risks counting things that aren't there."),
        Field("PPE_CHECK_INTERVAL_S", "Check every (seconds)", "number", default="1", min=0.5, max=10, step=0.5),
        Field("PPE_CONFIRM_CHECKS", "Checks to confirm a violation", "int", default="3", min=1, max=10,
              help="Consecutive failing checks before a violation is logged; stops brief occlusions alarming."),
        Field("PPE_COOLDOWN_S", "Re-log the same person after (s)", "number", default="300", min=0, max=3600,
              step=30),
        Field("PPE_MIN_PERSON_PX", "Minimum person height (px)", "int", default="80", min=20, max=600,
              help="People smaller than this are too far away to judge and are skipped."),
    )),
    Section("shelf", "Shelf camera", "Separate process: python -m phase8_shelf.app", "shelf",
            _camera_fields("SHELF_", "shelf-cam-1", required=False) + (
                Field("SHELF_CHECK_INTERVAL_S", "Stock check interval (s)", "number", default="30",
                      min=5, max=3600, step=5),
            ), camera_prefix="SHELF_"),
    Section("vehicle", "Vehicle camera", "Separate process: python -m phase13_vehicle.app", "vehicle",
            _camera_fields("VEHICLE_", "vehicle-cam-1", required=False) + (
                Field("VEHICLE_MIN_READINGS", "Plate readings to confirm", "int", default="3", min=1, max=10,
                      help="OCR readings that must agree before a plate is logged."),
            ), camera_prefix="VEHICLE_"),
)

FIELDS: dict[str, Field] = {f.key: f for s in SECTIONS for f in s.fields}
SECTION_OF: dict[str, Section] = {f.key: s for s in SECTIONS for f in s.fields}
PREFIXES = tuple(s.camera_prefix for s in SECTIONS if s.camera_prefix is not None)
_SOURCE_KEYS = ("CAMERA_SOURCE", "RTSP_URL", "CAMERA_IP", "RTSP_PORT", "CAMERA_RTSP_PATH", "CAMERA_CHANNEL")
_TRACKED_KEYS = sorted(
    {f.key for f in FIELDS.values() if f.type != "camera_url"}
    | {f"{p}{k}" for p in PREFIXES for k in _SOURCE_KEYS}
)


# -------------------------------------------------------------------- env file
def _quote(value: str) -> str:
    if value and not re.search(r"[\s#\"'\\]", value):
        return value
    if not value:
        return ""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _trailing_comment(rest: str) -> str:
    """The ``   # comment`` after a value, so a rewrite can keep it."""
    s = rest.lstrip()
    if s[:1] in ('"', "'"):
        q, i = s[0], 1
        while i < len(s):
            if s[i] == "\\" and q == '"':
                i += 2
                continue
            if s[i] == q:
                break
            i += 1
        tail = s[i + 1:]
    else:
        m = re.search(r"\s+#", rest)
        tail = rest[m.start():] if m else ""
    return tail if tail.strip().startswith("#") else ""


class EnvFile:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def values(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        return {k: (v or "") for k, v in dotenv_values(self.path).items()}

    def update(self, changes: dict[str, str]) -> None:
        """Rewrite the last active line of each key in place (dotenv's last one wins),
        keeping comments and layout; append keys that aren't in the file yet."""
        if not changes:
            return
        with self._lock:
            text = self.path.read_text(encoding="utf-8") if self.path.exists() else ""
            lines = text.splitlines()
            todo = dict(changes)
            for i in range(len(lines) - 1, -1, -1):
                m = re.match(r"^(\s*)(export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$", lines[i])
                if not m or m.group(3) not in todo:
                    continue
                indent, export, key, rest = m.groups()
                lines[i] = f"{indent}{export or ''}{key}={_quote(todo.pop(key))}{_trailing_comment(rest)}"
            if todo:
                if _MARKER not in lines:
                    lines += ["", _MARKER]
                lines += [f"{k}={_quote(v)}" for k, v in todo.items()]
            if self.path.exists():
                shutil.copy2(self.path, self.path.with_name(self.path.name + ".bak"))
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            os.replace(tmp, self.path)


# --------------------------------------------------------------- camera source
def _is_rtsp(url: str) -> bool:
    return url.lower().startswith(("rtsp://", "rtsps://"))


def _effective_source(prefix: str, vals: dict[str, str]) -> str:
    """What the camera actually connects to, mirroring phase1_camera.Config:
    CAMERA_SOURCE wins, then RTSP_URL, then the CAMERA_IP/channel builder."""
    g = lambda k: (vals.get(f"{prefix}{k}") or "").strip()  # noqa: E731
    if g("CAMERA_SOURCE"):
        return g("CAMERA_SOURCE")
    if g("RTSP_URL"):
        return g("RTSP_URL")
    if g("CAMERA_IP"):
        path = (g("CAMERA_RTSP_PATH") or "/Streaming/Channels/{channel}").replace(
            "{channel}", g("CAMERA_CHANNEL") or "101")
        return f"rtsp://{g('CAMERA_IP')}:{g('RTSP_PORT') or '554'}{'' if path.startswith('/') else '/'}{path}"
    return ""


def _redact(url: str) -> str:
    return _URL_CREDS_RE.sub(r"\1\2:***@", url)


def _unredact(new: str, stored: str) -> str:
    """A URL shown as ``user:***@`` and submitted unchanged keeps the stored password."""
    m_new, m_old = _URL_CREDS_RE.match(new), _URL_CREDS_RE.match(stored)
    if m_new and m_new.group(3) == "***" and m_old:
        return f"{m_new.group(1)}{m_new.group(2)}:{m_old.group(3)}@{new[m_new.end():]}"
    return new


def _check_source(url: str) -> str | None:
    if "://" in url:
        scheme = url.split("://", 1)[0].lower()
        if scheme not in ("rtsp", "rtsps", "http", "https"):
            return "Use an rtsp://, http:// or https:// URL."
        if not urlsplit(url).hostname:
            return "The URL is missing a host."
        return None
    if url.isdigit():
        return None
    return None if Path(url).is_file() else "No such video file on this machine."


def _source_changes(prefix: str, url: str) -> dict[str, str]:
    if not url:
        return {f"{prefix}CAMERA_SOURCE": "", f"{prefix}RTSP_URL": ""}
    if _is_rtsp(url):
        return {f"{prefix}RTSP_URL": url, f"{prefix}CAMERA_SOURCE": ""}
    return {f"{prefix}CAMERA_SOURCE": url}  # CAMERA_SOURCE outranks any RTSP_URL left behind


# ----------------------------------------------------------------- validation
def _norm(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _validate(f: Field, raw: Any) -> tuple[str | None, str | None]:
    """-> (value to store, error message)."""
    if isinstance(raw, bool) and f.type != "bool":
        return None, "Invalid value."
    v = ("" if raw is None else str(raw)) if f.type == "secret" else _norm(raw)  # passwords kept as typed
    if any(c in v for c in "\r\n\0"):
        return None, "Must be a single line."
    if f.type == "bool":
        if isinstance(raw, bool):
            return ("true" if raw else "false"), None
        if v.lower() in ("true", "false"):
            return v.lower(), None
        return None, "Must be on or off."
    if not v:
        return (None, "Required.") if f.required else ("", None)
    if f.type in ("int", "number"):
        try:
            n = int(v) if f.type == "int" else float(v)
        except ValueError:
            return None, "Must be a whole number." if f.type == "int" else "Must be a number."
        if (f.min is not None and n < f.min) or (f.max is not None and n > f.max):
            return None, f"Must be between {f.min:g} and {f.max:g}."
        return (str(n) if f.type == "int" else f"{n:g}"), None
    if f.type == "select" and v not in f.options:
        return None, f"Must be one of: {', '.join(f.options)}."
    if f.type == "camera_url":
        return (None, err) if (err := _check_source(v)) else (v, None)
    if f.check and (err := f.check(v)):
        return None, err
    return v, None


# ---------------------------------------------------------------- camera test
def _jpeg_data_url(jpg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpg).decode("ascii")


def _probe(cam: Config) -> dict:
    src = cam.opencv_source()
    t0 = time.monotonic()
    if isinstance(src, str) and "://" in src:
        if cam.uses_rtsp:
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = cam.ffmpeg_capture_options()
        params: list[int] = []
        for prop in ("CAP_PROP_OPEN_TIMEOUT_MSEC", "CAP_PROP_READ_TIMEOUT_MSEC"):
            if hasattr(cv2, prop):
                params += [getattr(cv2, prop), cam.open_timeout_ms]
        cap = cv2.VideoCapture(src, cv2.CAP_FFMPEG, params)
    else:
        cap = cv2.VideoCapture(src)
    try:
        if not cap.isOpened():
            hint = ("Check the IP, port, path and credentials." if cam.uses_rtsp
                    else "Check the address is reachable from this machine.")
            return {"ok": False, "error": f"Could not open the stream. {hint}"}
        frame = None
        for _ in range(5):  # the first RTSP frame can be a blank keyframe placeholder
            ok, f = cap.read()
            if ok and f is not None:
                frame = f
                break
        if frame is None:
            return {"ok": False, "error": "Connected, but no video frames arrived."}
        h, w = frame.shape[:2]
        if w > 480:
            frame = cv2.resize(frame, (480, int(h * 480 / w)))
        fps = cap.get(cv2.CAP_PROP_FPS)
        ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        return {
            "ok": True,
            "resolution": f"{w}x{h}",
            "fps": round(fps, 1) if fps and 0 < fps < 1000 else None,
            "elapsed_ms": round((time.monotonic() - t0) * 1000),
            "preview": _jpeg_data_url(jpg.tobytes()) if ok else None,
        }
    finally:
        cap.release()


# ---------------------------------------------------------------------- router
class SettingsUpdate(BaseModel):
    values: dict[str, Any]


class CameraTest(BaseModel):
    prefix: str = ""
    url: str
    user: str = ""
    password: str = ""
    transport: str = "tcp"


def create_router(env_path: Path, pipeline) -> APIRouter:
    env = EnvFile(env_path)
    allow_remote = (os.getenv("SETTINGS_ALLOW_REMOTE") or "").strip().lower() in ("1", "true", "yes", "on")
    startup_file = env.values()
    # os.environ holds what this process actually started with (a real environment
    # variable beats the .env file, so the two can differ).
    running = {k: os.environ.get(k, "") for k in _TRACKED_KEYS}
    probe_lock = threading.Lock()
    router = APIRouter(prefix="/api/settings")

    def is_local(request: Request) -> bool:
        return allow_remote or (request.client is not None and request.client.host in _LOOPBACK)

    def require_local(request: Request) -> None:
        if not is_local(request):
            raise HTTPException(403, "Settings can only be changed from the machine running the dashboard "
                                     "(set SETTINGS_ALLOW_REMOTE=true in .env to allow other machines).")

    def current(f: Field, vals: dict[str, str]) -> str:
        if f.type == "camera_url":
            return _effective_source(SECTION_OF[f.key].camera_prefix or "", vals)
        return vals.get(f.key, "")

    def payload(request: Request) -> dict:
        vals = env.values()
        restart = {"dashboard": False, "shelf": False, "vehicle": False}
        sections = []
        for s in SECTIONS:
            out = []
            for f in s.fields:
                now = current(f, vals)
                if f.type == "camera_url":
                    overridden = False
                    pending = _norm(now) != _norm(_effective_source(s.camera_prefix or "", running))
                else:
                    overridden = f.key in os.environ and _norm(running[f.key]) != _norm(startup_file.get(f.key))
                    pending = not overridden and _norm(now) != _norm(running[f.key])
                restart[s.process] |= pending
                item = {k: getattr(f, k) for k in ("key", "label", "type", "help", "default", "placeholder",
                                                   "min", "max", "step", "required")}
                item.update(options=list(f.options), pending=pending, overridden=overridden)
                if f.type == "secret":
                    item["is_set"] = bool(now)
                else:
                    item["value"] = _redact(now) if f.type == "camera_url" else now
                out.append(item)
            sections.append({"id": s.id, "title": s.title, "description": s.description,
                             "process": s.process, "camera_prefix": s.camera_prefix, "fields": out})
        return {"sections": sections, "restart": restart, "can_edit": is_local(request),
                "env_file": str(env_path)}

    @router.get("")
    def get_settings(request: Request) -> dict:
        return payload(request)

    @router.put("")
    def put_settings(body: SettingsUpdate, request: Request) -> dict:
        require_local(request)
        vals = env.values()
        changes: dict[str, str] = {}
        errors: dict[str, str] = {}
        for key, raw in body.values.items():
            f = FIELDS.get(key)
            if f is None:
                errors[key] = "Not an editable setting."
                continue
            if f.type == "secret" and not _norm(raw):
                continue  # blank means "keep the stored secret"
            if f.type == "camera_url":
                raw = _unredact(_norm(raw), current(f, vals))
                if _norm(raw) == _norm(current(f, vals)):
                    continue
            value, err = _validate(f, raw)
            if err:
                errors[key] = err
            elif f.type == "camera_url":
                changes.update(_source_changes(SECTION_OF[key].camera_prefix or "", value))
            else:
                changes[key] = value
        if errors:
            raise HTTPException(400, {"errors": errors})
        env.update(changes)
        return payload(request)

    @router.post("/test-camera")
    async def test_camera(body: CameraTest, request: Request) -> dict:
        require_local(request)
        if body.prefix not in PREFIXES:
            raise HTTPException(400, "Unknown camera.")
        vals = env.values()
        url = _unredact(_norm(body.url), _effective_source(body.prefix, vals))
        if not url:
            raise HTTPException(400, "Enter a stream source first.")
        if err := _check_source(url):
            raise HTTPException(400, err)
        # Base the probe on the running camera config (timeouts, log level) rather than
        # Config.load(), whose load_dotenv() would push newly saved keys into this
        # process's environment before a restart.
        live = pipeline.cfg.phase3.phase2.camera
        cam = dataclasses.replace(
            live,
            camera_source=None if _is_rtsp(url) else url,
            rtsp_url_override=url if _is_rtsp(url) else None,
            camera_user=_norm(body.user),
            camera_password=body.password or vals.get(f"{body.prefix}CAMERA_PASSWORD", ""),
            rtsp_transport=body.transport if body.transport in ("tcp", "udp") else "tcp",
        )

        # Testing the camera this dashboard is already streaming: reuse its frame
        # rather than opening a second connection (many cameras allow only one).
        if (body.prefix == "" and pipeline.alive and str(cam.opencv_source()) == str(live.opencv_source())
                and (jpg := pipeline.latest_jpeg()) is not None):
            return {"ok": True, "shared": True, "resolution": pipeline.status()["resolution"],
                    "fps": None, "elapsed_ms": 0, "preview": _jpeg_data_url(jpg), "source": _redact(url)}

        if not probe_lock.acquire(blocking=False):
            raise HTTPException(409, "A camera test is still running - try again in a few seconds.")

        def run() -> dict:
            try:
                return _probe(cam)
            finally:
                probe_lock.release()

        try:
            result = await asyncio.wait_for(asyncio.to_thread(run), timeout=cam.open_timeout_ms / 1000 + 10)
        except asyncio.TimeoutError:
            result = {"ok": False, "error": "Timed out waiting for the camera."}
        result["source"] = _redact(url)
        return result

    return router
