"""The API. Each phase added routes without changing the ones before it.

    GET  /                 connection + (if enabled) detection status, with a live view
    GET  /zones             a plain utility page for drawing zones on the camera view (Phase 4;
                           not the dashboard - that's Phase 7)
    GET  /api/status        camera JSON status (connected, fps, frame counts, last error, ...)
    GET  /live.mjpg          the raw camera stream
    GET  /live/frame.jpg    one current raw JPEG frame
    POST /api/reconnect     force an immediate camera reconnect attempt
    GET  /api/detections    latest detections (class, box, track_id, zone) + which spec classes
                           aren't detectable yet (Phase 2)
    GET  /live/annotated.mjpg   the live stream with detection boxes, track IDs and zone
                                outlines drawn (Phase 2-4)
    GET  /api/zones         list configured zones (Phase 4)
    POST /api/zones         add a zone: {camera_id, name, points: [[x, y], ...] in 0-1 fractions}
    PATCH /api/zones/{id}   rename a zone and/or replace its points
    DELETE /api/zones/{id}  remove a zone
    GET  /api/activities    activity engine status, currently-active activity per track, and
                           recently completed activities (Phase 5)
    GET  /api/events        persisted production events (Phase 6), filterable by camera/stage/
                           worker/since_ts
    GET  /api/analytics     per-stage counts/averages, worker active/idle seconds, and any
                           configured bottleneck comparisons (Phase 6/7, spec sections 17-18)
    GET  /api/workers       list manual worker-name assignments (spec section 13)
    POST /api/workers       assign a name to a track_id: {track_id, name}
    DELETE /api/workers/{track_id}   remove an assignment (reverts to the track-{id} fallback)
    GET  /dashboard         the production monitoring dashboard (Phase 7): live view with
                           toggleable overlays, per-worker timeline, activity history, analytics
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Protocol

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from ..activities.engine import ActivityEngine
from ..cameras.runner import CameraRunner
from ..detection.detector import ObjectDetector
from ..events.baselines import load_baselines
from ..events.store import EventStore
from ..workers.store import WorkerStore
from ..zones.store import ZoneError, ZoneStore
from .dashboard import render_dashboard
from .pages import render_status, render_zones

class _JpegSource(Protocol):
    def latest_jpeg(self) -> bytes | None: ...


def mjpeg_chunks(source: _JpegSource, idle_timeout: float = 30.0, poll_interval: float = 0.05):
    """The MJPEG body for any object exposing `.latest_jpeg()` (the raw `CameraRunner`, or the
    annotated `ObjectDetector`), as a plain generator independent of FastAPI/ASGI - so it can be
    unit tested directly (`next(mjpeg_chunks(fake_source))`) instead of through a real HTTP round
    trip. An infinite generator driven through Starlette's TestClient over httpx's ASGI transport
    was found to hang indefinitely in testing (confirmed by a thread-stack dump: the test process
    sat there for minutes with nothing progressing) - not a bug in this generator, but a real
    limitation of that combination, so the endpoint itself is verified with a live server instead
    (see garment_cv/README.md, "How this was tested")."""
    last = None
    idle_since = time.time()
    while True:
        jpg = source.latest_jpeg()
        if jpg is not None and jpg is not last:
            last = jpg
            idle_since = time.time()
            yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                  + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
        elif time.time() - idle_since > idle_timeout:
            break  # the source never produced a frame within idle_timeout - stop holding the connection open
        time.sleep(poll_interval)


class ZoneIn(BaseModel):
    camera_id: str = Field(..., min_length=1, max_length=120)
    name: str = Field(..., min_length=1, max_length=120)
    points: list[list[float]] = Field(..., min_length=3)
    activity: str | None = None


class ZonePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    points: list[list[float]] | None = Field(None, min_length=3)
    activity: str | None = None
    clear_activity: bool = False


def _zone_dict(z) -> dict:
    return {"id": z.id, "camera_id": z.camera_id, "name": z.name, "points": z.points, "activity": z.activity,
           "created_at": z.created_at}


class WorkerAssign(BaseModel):
    track_id: int
    name: str = Field(..., min_length=1, max_length=120)


class _ModeBody(BaseModel):
    mode: str


class _HoldBody(BaseModel):
    on: bool


class _ManualBody(BaseModel):
    action: str
    step: int


def create_app(runner: CameraRunner, detector: ObjectDetector | None = None, zone_store: ZoneStore | None = None,
              start_camera: bool = True, activity_engine: ActivityEngine | None = None,
              event_store: EventStore | None = None, worker_store: WorkerStore | None = None,
              odoo_bridge=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if detector is not None:
            detector.start()
            if detector.enabled:
                runner.frame_hooks.append(detector.observe)
        if start_camera and runner.cfg.source:
            runner.start()  # runs in the background regardless of whether it connects on the first try
        if odoo_bridge is not None:
            odoo_bridge.start()
        yield
        if odoo_bridge is not None:
            odoo_bridge.stop()
        runner.stop()

    app = FastAPI(title="Garment Manufacturing CV", lifespan=lifespan)
    app.state.runner = runner
    app.state.detector = detector
    app.state.zone_store = zone_store
    app.state.activity_engine = activity_engine
    app.state.event_store = event_store
    app.state.worker_store = worker_store
    app.state.odoo_bridge = odoo_bridge

    @app.exception_handler(ZoneError)
    async def _zone_error(_request, e: ZoneError):
        return JSONResponse({"detail": str(e)}, status_code=400)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        st = runner.status()
        checks: list[tuple[str, str, str]] = [
            ("ok" if st["connected"] else "bad", "Camera",
             "The camera is connected and sending video." if st["connected"]
             else "The camera is not connected." + (f" {st['last_error']}" if st.get("last_error") else ""))]
        img_src, feed_note = "/live.mjpg", "Detection is not running, so this is the plain camera picture."
        if detector is not None:
            if detector.enabled:
                dst = detector.status()
                img_src = "/live/annotated.mjpg"
                feed_note = "Detected people and objects are outlined on the picture."
                looking = ", ".join(dst["detected_classes"]) or "nothing yet"
                where = {"0": "graphics card", "cpu": "processor"}.get(str(dst["device"]), f"device {dst['device']}")
                detail = f"Detection is on, running on the {where}. Looking for: {looking}."
                if dst["not_yet_detectable"]:
                    detail += f" Not detectable yet: {', '.join(dst['not_yet_detectable'])}."
                checks.append(("ok", "Object detection", detail))
                if dst["tracking_enabled"]:
                    checks.append(("ok", "Tracking", f"People and objects are followed from frame to frame ({str(dst['tracker']).removesuffix('.yaml')})."))
                n_zones = len(zone_store.list_zones(runner.cfg.camera_id)) if zone_store else 0
                checks.append(("ok" if n_zones else "warn", "Zones",
                               (f"{n_zones} zone{'s' if n_zones != 1 else ''} configured. " if n_zones else "No zones configured yet. ")
                               + "Zones tell the system where each kind of work happens."))
                if activity_engine is not None and activity_engine.cfg.enabled:
                    ast = activity_engine.status()
                    checks.append(("ok", "Activity recognition", "On. Can recognise: " + ", ".join(a.replace("_", " ").lower() for a in ast["reachable_now"]) + "."))
            else:
                checks.append(("warn", "Object detection", f"Detection is off: {detector.disabled_reason}"))
        if odoo_bridge is not None:
            ost = odoo_bridge.status()
            if not ost["configured"]:
                checks.append(("warn", "Odoo", "Not set up yet. Add the Odoo address, database, API key and work center in the settings file."))
            elif ost["odoo_ok"] is False:
                checks.append(("bad", "Odoo", f"Cannot reach Odoo. Updates wait in the queue. Mode: {ost['mode']}."))
            else:
                checks.append(("ok" if ost["mode"] != "off" else "warn", "Odoo",
                               f"Connected. Mode: {ost['mode']}. Work center: {ost['workcenter_code']}."))
        up = int(st.get("stream_uptime_s") or 0)
        rows = [("connected", "Connection", "Connected" if st["connected"] else "Not connected"),
                ("resolution", "Picture size", str(st.get("resolution") or "-")),
                ("actual_fps", "Frames per second", f"{st['actual_fps']:.1f} per second" if st.get("actual_fps") is not None else "-"),
                ("frames_received", "Frames received", str(st["frames_received"])),
                ("frames_failed", "Frames failed", str(st["frames_failed"])),
                ("reconnections", "Reconnections", str(st["reconnections"])),
                ("stream_uptime_s", "Connected for", f"{up // 3600} h {up % 3600 // 60} min" if up >= 3600 else f"{up // 60} min {up % 60} s"),
                ("last_error", "Last error", str(st.get("last_error") or "None")),
                ("camera_id", "Camera ID", str(st["camera_id"])),
                ("source", "Source", str(st["source"]))]
        return render_status(camera_name=st["camera_name"], checks=checks, img_src=img_src, feed_note=feed_note,
                             rows=rows, dashboard=event_store is not None and activity_engine is not None)

    @app.get("/api/status")
    def status() -> dict:
        return runner.status()

    @app.get("/live/frame.jpg")
    def frame() -> Response:
        jpg = runner.latest_jpeg()
        if jpg is None:
            raise HTTPException(503, "no frame available yet")
        return Response(jpg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/live.mjpg")
    def live() -> StreamingResponse:
        return StreamingResponse(mjpeg_chunks(runner), media_type="multipart/x-mixed-replace; boundary=frame")

    @app.post("/api/reconnect")
    def reconnect() -> dict:
        runner.reconnect_now()
        return {"ok": True, "note": "reconnect requested; check /api/status shortly"}

    if detector is not None:
        @app.get("/api/detections")
        def detections() -> dict:
            return detector.status()

        @app.get("/live/annotated.mjpg")
        def annotated() -> StreamingResponse:
            if not detector.enabled:
                raise HTTPException(503, f"detection is off: {detector.disabled_reason}")
            return StreamingResponse(mjpeg_chunks(detector), media_type="multipart/x-mixed-replace; boundary=frame")

    if zone_store is not None:
        @app.get("/zones", response_class=HTMLResponse)
        def zones_page() -> str:
            return render_zones(camera_name=runner.cfg.camera_name, camera_id=runner.cfg.camera_id,
                                dashboard=event_store is not None and activity_engine is not None)

        @app.get("/api/zones")
        def list_zones(camera_id: str | None = None) -> list[dict]:
            return [_zone_dict(z) for z in zone_store.list_zones(camera_id)]

        @app.post("/api/zones")
        def add_zone(body: ZoneIn) -> dict:
            return _zone_dict(zone_store.add_zone(body.camera_id, body.name, body.points, activity=body.activity))

        @app.patch("/api/zones/{zone_id}")
        def update_zone(zone_id: int, body: ZonePatch) -> dict:
            return _zone_dict(zone_store.update_zone(zone_id, name=body.name, points=body.points,
                                                      activity=body.activity, clear_activity=body.clear_activity))

        @app.delete("/api/zones/{zone_id}")
        def delete_zone(zone_id: int) -> dict:
            zone_store.delete_zone(zone_id)
            return {"ok": True}

    if odoo_bridge is not None:
        @app.get("/api/odoo/status")
        def odoo_status() -> dict:
            return odoo_bridge.status()

        @app.get("/api/odoo/audit")
        def odoo_audit(limit: int = 50) -> list[dict]:
            return odoo_bridge.audit_log(min(max(limit, 1), 500))

        @app.post("/api/odoo/mode")
        def odoo_mode(body: _ModeBody) -> dict:
            try:
                odoo_bridge.set_mode(body.mode)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            return odoo_bridge.status()

        @app.post("/api/odoo/hold")
        def odoo_hold(body: _HoldBody) -> dict:
            odoo_bridge.hold(body.on)
            return odoo_bridge.status()

        @app.post("/api/odoo/manual")
        def odoo_manual(body: _ManualBody) -> dict:
            try:
                odoo_bridge.manual(body.action, body.step)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            return odoo_bridge.status()

        @app.post("/api/odoo/retry")
        def odoo_retry() -> dict:
            return {"requeued": odoo_bridge.retry_failed()}

        @app.post("/api/odoo/discard")
        def odoo_discard() -> dict:
            return {"discarded": odoo_bridge.discard_failed()}

    if activity_engine is not None:
        @app.get("/api/activities")
        def activities() -> dict:
            return {"status": activity_engine.status(), "current": activity_engine.current(),
                    "recent": activity_engine.recent()}

    if event_store is not None:
        @app.get("/api/events")
        def list_events(camera_id: str | None = None, stage: str | None = None, worker: str | None = None,
                        since_ts: float | None = None, limit: int = 100) -> list[dict]:
            events = event_store.list(camera_id=camera_id, stage=stage, worker=worker,
                                      since_ts=since_ts, limit=limit)
            return [e.to_dict() for e in events]

        @app.get("/api/analytics")
        def analytics(since_ts: float | None = None) -> dict:
            summary = event_store.stage_summary(since_ts=since_ts)
            baselines = load_baselines()
            bottlenecks = []
            for stage, baseline_s in baselines.items():
                if baseline_s is None or stage not in summary:
                    continue
                actual = summary[stage]["avg_s"]
                bottlenecks.append({"stage": stage, "baseline_s": baseline_s, "actual_avg_s": actual,
                                    "status": "Needs Investigation" if actual > baseline_s else "OK"})
            return {"stage_summary": summary,
                    "worker_active_seconds": event_store.worker_active_seconds(since_ts=since_ts),
                    "worker_idle_seconds": event_store.worker_idle_seconds(since_ts=since_ts),
                    "bottlenecks": bottlenecks}

    if worker_store is not None:
        @app.get("/api/workers")
        def list_workers() -> list[dict]:
            return worker_store.list()

        @app.post("/api/workers")
        def assign_worker(body: WorkerAssign) -> dict:
            try:
                return worker_store.assign(body.track_id, body.name)
            except ValueError as e:
                raise HTTPException(400, str(e))

        @app.delete("/api/workers/{track_id}")
        def unassign_worker(track_id: int) -> dict:
            worker_store.unassign(track_id)
            return {"ok": True}

    if event_store is not None and activity_engine is not None:
        @app.get("/dashboard", response_class=HTMLResponse)
        def dashboard_page() -> str:
            return render_dashboard(camera_name=runner.cfg.camera_name, camera_id=runner.cfg.camera_id)

    return app
