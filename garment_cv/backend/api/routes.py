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
from .dashboard import DASHBOARD_PAGE

_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Garment CV - Camera</title>
<meta http-equiv="refresh" content="5">
<style>
body{{font:14px/1.5 system-ui,sans-serif;background:#0d1117;color:#e6edf3;margin:0;padding:24px}}
.wrap{{max-width:760px;margin:0 auto}}
h1{{font-size:1.1rem;margin:0 0 4px}}
a{{color:#4c9eff}}
.sub{{color:#8b949e;font-size:.8rem;margin:0 0 18px}}
.pill{{display:inline-block;padding:3px 10px;border-radius:999px;font-size:.76rem;margin:0 6px 16px 0}}
.ok{{background:#1a7f37;color:#fff}} .bad{{background:#cf222e;color:#fff}} .warn{{background:#9a6700;color:#fff}}
img{{width:100%;border-radius:8px;background:#000;display:block}}
table{{width:100%;border-collapse:collapse;margin-top:16px;font-size:.82rem}}
td{{padding:5px 8px;border-bottom:1px solid #21262d}}
td.k{{color:#8b949e;width:40%}}
.note{{color:#8b949e;font-size:.76rem;line-height:1.5;margin-top:14px}}
</style></head><body><div class="wrap">
<h1>Garment CV — {camera_name}</h1>
<p class="sub">{phase_note} <a href="/zones">Draw zones &rarr;</a></p>
{pills}
<img src="{img_src}" alt="live view">
<table>{rows}</table>
{detect_note}
</div></body></html>"""

_ZONES_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Garment CV - Zones</title>
<style>
body{{font:14px/1.5 system-ui,sans-serif;background:#0d1117;color:#e6edf3;margin:0;padding:24px}}
.wrap{{max-width:820px;margin:0 auto}} a{{color:#4c9eff}}
h1{{font-size:1.1rem;margin:0 0 4px}} p.note{{color:#8b949e;font-size:.8rem}}
.stage{{position:relative;background:#000;border-radius:8px;overflow:hidden;line-height:0}}
.stage img{{width:100%;display:block}} .stage canvas{{position:absolute;inset:0;width:100%;height:100%;cursor:crosshair}}
.row{{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap;align-items:center}}
input{{background:#161b22;border:1px solid #30363d;color:#e6edf3;border-radius:6px;padding:7px 10px;font:inherit}}
button{{background:#21262d;border:1px solid #30363d;color:#e6edf3;border-radius:6px;padding:7px 12px;font:inherit;cursor:pointer}}
button:hover{{border-color:#4c9eff}} button.primary{{background:#1f6feb;border-color:#1f6feb}}
ul{{list-style:none;padding:0;margin-top:14px}}
li{{display:flex;justify-content:space-between;align-items:center;padding:8px 10px;border:1px solid #21262d;border-radius:6px;margin-bottom:6px}}
li button{{padding:3px 9px;font-size:.78rem}}
</style></head><body><div class="wrap">
<h1>Zones — {camera_name}</h1>
<p class="note">Click on the picture to add points (3 or more), name the zone below, then Save. Existing zones are
outlined in orange; the one you're drawing is green. <a href="/">&larr; Back</a></p>
<div class="stage"><img id="bg" src="/live/frame.jpg?t=0" alt="camera view"><canvas id="cv"></canvas></div>
<div class="row">
  <input id="name" placeholder="Zone name, e.g. Cutting Zone" style="flex:1;min-width:200px">
  <select id="activity">
    <option value="">No activity (just a zone)</option>
    <option value="cutting">Cutting</option>
    <option value="marking">Marking</option>
    <option value="marking,cutting">Marking + Cutting (one table)</option>
    <option value="stitching">Stitching</option>
    <option value="piece_handling">Piece handling</option>
  </select>
  <button class="primary" id="save">Save zone</button>
  <button id="undo">Undo point</button>
  <button id="clear">Clear</button>
  <button id="refresh">Refresh picture</button>
</div>
<ul id="list"></ul>
<script>
const CAMERA_ID = {camera_id_js};
const img = document.getElementById('bg'), cv = document.getElementById('cv'), list = document.getElementById('list');
let points = [], zones = [];

function resize() {{ cv.width = img.clientWidth; cv.height = img.clientHeight; draw(); }}
window.addEventListener('resize', resize);
img.addEventListener('load', resize);

function poly(ctx, pts, color, fill) {{
  if (!pts.length) return;
  ctx.beginPath();
  pts.forEach(([x, y], i) => {{ const px = x * cv.width, py = y * cv.height; i ? ctx.lineTo(px, py) : ctx.moveTo(px, py); }});
  if (pts.length > 2) ctx.closePath();
  ctx.fillStyle = fill; ctx.fill(); ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.stroke();
}}

function draw() {{
  const ctx = cv.getContext('2d');
  ctx.clearRect(0, 0, cv.width, cv.height);
  for (const z of zones) {{
    poly(ctx, z.points, '#f0883e', 'rgba(240,136,62,.15)');
    if (z.points.length) {{ ctx.fillStyle = '#f0883e'; ctx.font = '12px sans-serif';
      ctx.fillText(z.name, z.points[0][0] * cv.width + 4, z.points[0][1] * cv.height - 6); }}
  }}
  poly(ctx, points, '#3fb950', 'rgba(63,185,80,.25)');
  ctx.fillStyle = '#3fb950';
  for (const [x, y] of points) {{ ctx.beginPath(); ctx.arc(x * cv.width, y * cv.height, 4, 0, 7); ctx.fill(); }}
}}

cv.addEventListener('click', (e) => {{
  const r = cv.getBoundingClientRect();
  points.push([+((e.clientX - r.left) / r.width).toFixed(4), +((e.clientY - r.top) / r.height).toFixed(4)]);
  draw();
}});
document.getElementById('undo').onclick = () => {{ points.pop(); draw(); }};
document.getElementById('clear').onclick = () => {{ points = []; draw(); }};
document.getElementById('refresh').onclick = () => {{ img.src = '/live/frame.jpg?t=' + Date.now(); }};

async function loadZones() {{
  const r = await fetch('/api/zones?camera_id=' + encodeURIComponent(CAMERA_ID));
  zones = await r.json();
  list.innerHTML = zones.map(z => `<li><span>${{z.name}} (${{z.points.length}} points)${{z.activity ? ' &mdash; ' + z.activity : ''}}</span>
    <button data-id="${{z.id}}">Delete</button></li>`).join('') || '<li><span style="color:#8b949e">No zones yet.</span></li>';
  draw();
}}
list.addEventListener('click', async (e) => {{
  const id = e.target.dataset.id;
  if (!id) return;
  await fetch('/api/zones/' + id, {{ method: 'DELETE' }});
  loadZones();
}});
document.getElementById('save').onclick = async () => {{
  const name = document.getElementById('name').value.trim();
  if (!name) return alert('Give the zone a name.');
  if (points.length < 3) return alert('Click at least 3 points first.');
  const activity = document.getElementById('activity').value || null;
  const res = await fetch('/api/zones', {{ method: 'POST', headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{ camera_id: CAMERA_ID, name, points, activity }}) }});
  if (!res.ok) {{ const b = await res.json().catch(() => ({{}})); return alert('Could not save: ' + (b.detail || res.status)); }}
  points = []; document.getElementById('name').value = ''; document.getElementById('activity').value = '';
  loadZones();
}};
resize(); loadZones();
</script>
</div></body></html>"""


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
        pills = [f"<span class='pill {'ok' if st['connected'] else 'bad'}'>"
                f"{'connected' if st['connected'] else 'not connected'}</span>"]
        note = "Phase 1: camera module only. No detection, zones or activity logic yet."
        img_src, detect_note = "/live.mjpg", ""
        if detector is not None:
            note = "Phase 1-5: camera, object detection (COCO stand-in), tracking, zones and activity detection."
        if event_store is not None:
            note = ("Phase 1-7: camera, object detection (COCO stand-in), tracking, zones, activity "
                    "detection, production events + database, and the <a href='/dashboard'>dashboard</a>.")
        if detector is not None:
            if detector.enabled:
                dst = detector.status()
                pills.append(f"<span class='pill ok'>detection on ({dst['device']})</span>")
                if dst["tracking_enabled"]:
                    pills.append(f"<span class='pill ok'>tracking on ({dst['tracker']})</span>")
                img_src = "/live/annotated.mjpg"
                n_zones = len(zone_store.list_zones(runner.cfg.camera_id)) if zone_store else 0
                pills.append(f"<span class='pill {'ok' if n_zones else 'warn'}'>{n_zones} zone(s) configured</span>")
                detect_note = (f"<p class='note'>Detecting: {', '.join(dst['detected_classes'])}. "
                              f"Not yet detectable (no COCO match - needs real training data): "
                              f"{', '.join(dst['not_yet_detectable'])}.</p>")
                if activity_engine is not None and activity_engine.cfg.enabled:
                    ast = activity_engine.status()
                    pills.append(f"<span class='pill ok'>activity detection on</span>")
                    detect_note += (f"<p class='note'>Activity rules reachable now (given the COCO stand-in): "
                                   f"{', '.join(ast['reachable_now'])}. Dormant (real, tested, but no COCO class "
                                   f"exists yet for them): {', '.join(ast['dormant'])}.</p>")
            else:
                pills.append(f"<span class='pill warn'>detection off — {detector.disabled_reason}</span>")
        rows = "".join(f"<tr><td class='k'>{k}</td><td>{v}</td></tr>" for k, v in st.items())
        return _PAGE.format(camera_name=st["camera_name"], phase_note=note, pills="".join(pills),
                            img_src=img_src, rows=rows, detect_note=detect_note)

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
        import json as _json

        @app.get("/zones", response_class=HTMLResponse)
        def zones_page() -> str:
            return _ZONES_PAGE.format(camera_name=runner.cfg.camera_name,
                                      camera_id_js=_json.dumps(runner.cfg.camera_id))

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
        import json as _json

        @app.get("/dashboard", response_class=HTMLResponse)
        def dashboard_page() -> str:
            return DASHBOARD_PAGE.format(camera_name=runner.cfg.camera_name,
                                         camera_id_js=_json.dumps(runner.cfg.camera_id))

    return app
