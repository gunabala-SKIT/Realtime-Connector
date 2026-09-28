"""HTTP API + dashboard for the Manufacturing Process Intelligence Platform.

    python -m manufacturing_intel            # http://127.0.0.1:8100

Access rules (this serves factory data, so the defaults are conservative):
  * reads (GET) are open to whoever can reach the port; by default it only listens on 127.0.0.1
  * configuration changes need a local client, unless MPI_ADMIN_REMOTE=true
  * event ingest (/api/ingest/*) from another machine needs the MPI_API_KEY header X-API-Key;
    without a key configured only local clients may ingest
"""
from __future__ import annotations

import hmac
import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import periods
from .alerts import AlertEngine
from .analytics import Analytics
from .assistant import Assistant
from .config import Settings
from .engine import ProductionEngine
from .store import DEFAULT_SHIFTS, REJECT_REASONS, Store, StoreError
from .vision import CameraManager, grab_frame, redact

log = logging.getLogger("mpi.server")
WEB = Path(__file__).resolve().parent / "web"
LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}


# --------------------------------------------------------------------- bodies
class FactoryIn(BaseModel):
    name: str | None = Field(None, max_length=120)
    shifts: list[dict] | None = None


class NameIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)


class ProductIn(BaseModel):
    code: str = Field(..., min_length=1, max_length=40)
    name: str = Field(..., min_length=1, max_length=120)
    target_cycle_min: float | None = Field(None, gt=0, le=100000)


class ProductPatch(BaseModel):
    name: str | None = Field(None, max_length=120)
    target_cycle_min: float | None = Field(None, gt=0, le=100000)
    clear_target: bool = False


class StationIn(BaseModel):
    line_id: int
    name: str = Field(..., min_length=1, max_length=120)
    kind: str = "process"
    expected_process_min: float | None = Field(None, gt=0, le=100000)
    expected_wait_qty: int | None = Field(None, ge=0, le=100000)
    target_units_per_h: float | None = Field(None, gt=0, le=1e6)
    reject_threshold_pct: float | None = Field(None, ge=0, le=100)
    idle_alert_min: float | None = Field(None, gt=0, le=100000)
    capacity: int | None = Field(None, ge=1, le=500)
    camera_id: int | None = None
    zone: list[list[float]] | None = None


class StationPatch(BaseModel):
    name: str | None = Field(None, max_length=120)
    expected_process_min: float | None = Field(None, gt=0, le=100000)
    expected_wait_qty: int | None = Field(None, ge=0, le=100000)
    target_units_per_h: float | None = Field(None, gt=0, le=1e6)
    reject_threshold_pct: float | None = Field(None, ge=0, le=100)
    idle_alert_min: float | None = Field(None, gt=0, le=100000)
    capacity: int | None = Field(None, ge=1, le=500)
    camera_id: int | None = None
    zone: list[list[float]] | None = None
    clear: list[str] = []


class RouteIn(BaseModel):
    line_id: int
    product_id: int
    station_ids: list[int] = Field(..., min_length=3, max_length=60)
    step_minutes: dict[int, float] | None = None


class BatchIn(BaseModel):
    product_id: int
    line_id: int
    qty_planned: int | None = Field(None, gt=0, le=10_000_000)
    code: str | None = Field(None, max_length=60)


class CameraIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    source: str = Field(..., min_length=1, max_length=1000)
    line_id: int | None = None
    detector: str = "motion"
    params: dict[str, Any] = {}
    decode_qr: bool = False
    enabled: bool = True


class CameraPatch(BaseModel):
    name: str | None = Field(None, max_length=120)
    source: str | None = Field(None, max_length=1000)
    line_id: int | None = None
    detector: str | None = None
    params: dict[str, Any] | None = None
    decode_qr: bool | None = None
    enabled: bool | None = None


class EventIn(BaseModel):
    type: str
    station_id: int | None = None
    line_id: int | None = None
    station: str | None = Field(None, max_length=120)
    unit_code: str | None = Field(None, max_length=64)
    ts: float | None = None
    product: str | None = Field(None, max_length=40)
    batch: str | None = Field(None, max_length=60)
    reason: str | None = Field(None, max_length=120)
    source: str = Field("api", max_length=40)


class QuestionIn(BaseModel):
    question: str = Field(..., max_length=500)
    line_id: int | None = None


# ------------------------------------------------------------------------ app
def create_app(settings: Settings | None = None, store: Store | None = None, start_background: bool = True) -> FastAPI:
    settings = settings or Settings.load()
    store = store or Store(settings.db_path)
    store.ensure_factory()
    engine = ProductionEngine(store)
    analytics = Analytics(store)
    alerts = AlertEngine(store, analytics)
    assistant = Assistant(analytics)
    cameras = CameraManager(store, engine)
    stop = threading.Event()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_background:
            threading.Thread(target=alerts.run_forever, args=(stop, settings.alert_interval_s), daemon=True,
                             name="alerts").start()
            cameras.start_enabled()
        yield
        stop.set()
        cameras.stop_all()

    app = FastAPI(title="Manufacturing Process Intelligence Platform", lifespan=lifespan)
    app.state.store, app.state.engine, app.state.cameras = store, engine, cameras

    # ---- access control -------------------------------------------------
    def is_local(request: Request) -> bool:
        return (request.client.host if request.client else "") in LOOPBACK

    def admin(request: Request) -> None:
        if not (is_local(request) or settings.admin_remote):
            raise HTTPException(403, "Configuration can only be changed from the machine running the server "
                                     "(set MPI_ADMIN_REMOTE=true to allow other machines).")

    def ingest_auth(request: Request) -> None:
        key = request.headers.get("x-api-key")
        if settings.api_key:
            if not key or not hmac.compare_digest(key, settings.api_key):
                raise HTTPException(401, "Missing or wrong X-API-Key.")
        elif not is_local(request):
            raise HTTPException(403, "Ingest from other machines needs MPI_API_KEY to be set on the server.")

    @app.exception_handler(StoreError)
    async def _store_error(_: Request, e: StoreError):
        return JSONResponse({"detail": str(e)}, status_code=400)

    @app.exception_handler(periods.PeriodError)
    async def _period_error(_: Request, e: periods.PeriodError):
        return JSONResponse({"detail": str(e)}, status_code=400)

    def scope(line_id: int | None, product_id: int | None) -> dict:
        if line_id is not None and not store.get_line(line_id):
            raise HTTPException(404, "no such line")
        if product_id is not None and not store.get_product(product_id):
            raise HTTPException(404, "no such product")
        return {"line_id": line_id, "product_id": product_id}

    def window(period: str, start: str | None, end: str | None) -> periods.Window:
        return analytics.window(period, start, end)

    def default_line(line_id: int | None) -> int:
        if line_id:
            if not store.get_line(line_id):
                raise HTTPException(404, "no such line")
            return line_id
        lines = store.list_lines()
        if not lines:
            raise HTTPException(409, "no production line configured yet (Setup)")
        return lines[0]["id"]

    # ---- health / config ---------------------------------------------------
    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "time": time.time(), "simulated_data": analytics.has_simulated_data(),
                "ingest_key_required": bool(settings.api_key)}

    @app.get("/api/config")
    def get_config() -> dict:
        f = store.ensure_factory()
        cams = store.list_cameras()
        for c in cams:
            c["source"] = redact(c["source"])
        return {"factory": f, "lines": store.list_lines(), "products": store.list_products(),
                "stations": store.list_stations(), "routes": store.list_routes(), "cameras": cams,
                "periods": list(periods.PERIODS), "reject_reasons": list(REJECT_REASONS),
                "default_shifts": DEFAULT_SHIFTS, "simulated_data": analytics.has_simulated_data()}

    @app.put("/api/factory", dependencies=[Depends(admin)])
    def put_factory(body: FactoryIn) -> dict:
        return store.update_factory(body.name, body.shifts)

    @app.post("/api/lines", dependencies=[Depends(admin)])
    def post_line(body: NameIn) -> dict:
        return store.add_line(body.name)

    @app.patch("/api/lines/{line_id}", dependencies=[Depends(admin)])
    def patch_line(line_id: int, body: NameIn) -> dict:
        return store.rename_line(line_id, body.name)

    @app.post("/api/products", dependencies=[Depends(admin)])
    def post_product(body: ProductIn) -> dict:
        return store.add_product(body.code, body.name, body.target_cycle_min)

    @app.patch("/api/products/{pid}", dependencies=[Depends(admin)])
    def patch_product(pid: int, body: ProductPatch) -> dict:
        return store.update_product(pid, body.name, body.target_cycle_min, body.clear_target)

    @app.post("/api/stations", dependencies=[Depends(admin)])
    def post_station(body: StationIn) -> dict:
        d = body.model_dump(exclude_none=True)
        return store.add_station(d.pop("line_id"), d.pop("name"), d.pop("kind", "process"), **d)

    @app.patch("/api/stations/{sid}", dependencies=[Depends(admin)])
    def patch_station(sid: int, body: StationPatch) -> dict:
        return store.update_station(sid, **body.model_dump(exclude_none=True))

    @app.delete("/api/stations/{sid}", dependencies=[Depends(admin)])
    def delete_station(sid: int) -> dict:
        store.delete_station(sid)
        return {"ok": True}

    @app.put("/api/routes", dependencies=[Depends(admin)])
    def put_route(body: RouteIn) -> dict:
        return store.save_route(body.line_id, body.product_id, body.station_ids, body.step_minutes)

    # ---- batches -------------------------------------------------------------
    @app.get("/api/batches")
    def list_batches(line_id: int | None = None, status: str | None = None, limit: int = Query(200, le=1000)) -> list[dict]:
        return store.list_batches(line_id, status, limit)

    @app.post("/api/batches", dependencies=[Depends(admin)])
    def post_batch(body: BatchIn) -> dict:
        return store.add_batch(body.product_id, body.line_id, body.qty_planned, body.code)

    @app.post("/api/batches/{bid}/close", dependencies=[Depends(admin)])
    def close_batch(bid: int) -> dict:
        return store.close_batch(bid)

    @app.get("/api/batches/{bid}/flow")
    def batch_flow(bid: int) -> dict:
        f = analytics.batch_flow(bid)
        if not f:
            raise HTTPException(404, "no such batch")
        return f

    # ---- ingest ----------------------------------------------------------------
    def _resolve_station(e: EventIn) -> int:
        if e.station_id:
            return e.station_id
        if e.line_id and e.station:
            for s in store.list_stations(e.line_id):
                if s["name"].lower() == e.station.strip().lower():
                    return s["id"]
        raise HTTPException(422, "give station_id, or line_id + station (name)")

    def _apply(e: EventIn) -> dict:
        r = engine.record(e.type, _resolve_station(e), ts=e.ts, unit_code=e.unit_code, product=e.product,
                          batch=e.batch, reason=e.reason, source=e.source)
        return r

    @app.post("/api/ingest/event")
    def ingest_event(e: EventIn, request: Request, _: None = Depends(ingest_auth)) -> JSONResponse:
        r = _apply(e)
        return JSONResponse(r, status_code=422 if r["result"] == "error" else 200)

    @app.post("/api/ingest/events")
    def ingest_events(items: list[EventIn], request: Request, _: None = Depends(ingest_auth)) -> dict:
        if len(items) > 500:
            raise HTTPException(413, "at most 500 events per request")
        out = [_apply(e) for e in items]
        summary: dict[str, int] = {}
        for r in out:
            summary[r["result"]] = summary.get(r["result"], 0) + 1
        return {"summary": summary, "results": out}

    @app.post("/api/operator/event", dependencies=[Depends(admin)])
    def operator_event(e: EventIn) -> JSONResponse:
        """The Operator console (manual entry from the dashboard)."""
        e.source = "operator"
        r = _apply(e)
        return JSONResponse(r, status_code=422 if r["result"] == "error" else 200)

    @app.get("/api/events")
    def recent_events(limit: int = Query(100, le=500), result: str | None = None, line_id: int | None = None) -> list[dict]:
        where, p = [], []
        if result:
            where.append("e.result=?"); p.append(result)
        if line_id:
            where.append("s.line_id=?"); p.append(line_id)
        return store.q("SELECT e.id, e.ts, e.type, s.name station, e.unit_code, e.source, e.result, e.detail, e.reason "
                       "FROM events e LEFT JOIN stations s ON s.id=e.station_id "
                       + ("WHERE " + " AND ".join(where) if where else "") + " ORDER BY e.id DESC LIMIT ?", (*p, limit))

    # ---- analytics ----------------------------------------------------------------
    @app.get("/api/overview")
    def overview(period: str = "today", start: str | None = None, end: str | None = None, line_id: int | None = None,
                 product_id: int | None = None, compare: bool = True) -> dict:
        lid = default_line(line_id)
        sc = scope(lid, product_id)
        w = window(period, start, end)
        now = time.time()
        k = analytics.kpis(w, **sc)
        out: dict = {"now": now, "line": store.get_line(lid), "window": w.to_dict(now), "kpis": k,
                     "stations": analytics.stations(w, lid, product_id), "simulated_data": analytics.has_simulated_data(),
                     "alerts": [a for a in store.list_alerts(limit=100) if a["status"] != "resolved"][:12],
                     "data_quality": analytics.data_quality(w, lid), "bottleneck": None, "previous": None}
        # one flow per product route: mixing the stations of different routes into one row would
        # draw arrows between stations no product actually travels between
        routes = [r for r in store.list_routes(lid) if not product_id or r["product_id"] == product_id]
        out["flows"] = [{"product_id": r["product_id"], "product_code": r["product_code"], "product_name": r["product_name"],
                         "stations": out["stations"] if len(routes) == 1 else analytics.stations(w, lid, r["product_id"])}
                        for r in routes]
        if w.live(now) or period in ("last_hour", "last_24h"):
            out["bottleneck"] = analytics.bottleneck(lid, product_id)
        if compare:
            pw = analytics.previous_window(w)
            c = analytics.compare(w, pw, lid, product_id)
            out["previous"] = {"window": c["b"], "kpis": c["b_kpis"], "changes": c["stages"][:3]}
        return out

    @app.get("/api/kpis")
    def kpis(period: str = "today", start: str | None = None, end: str | None = None, line_id: int | None = None,
             product_id: int | None = None, batch_id: int | None = None) -> dict:
        return analytics.kpis(window(period, start, end), line_id, product_id, batch_id)

    @app.get("/api/stations")
    def stations(period: str = "today", start: str | None = None, end: str | None = None, line_id: int | None = None,
                 product_id: int | None = None, batch_id: int | None = None) -> list[dict]:
        return analytics.stations(window(period, start, end), default_line(line_id), product_id, batch_id)

    @app.get("/api/compare")
    def compare(a: str = "today", b: str | None = None, start_a: str | None = None, end_a: str | None = None,
                start_b: str | None = None, end_b: str | None = None, line_id: int | None = None,
                line_b: int | None = None, product_id: int | None = None, like_for_like: bool = True) -> dict:
        wa = window(a, start_a, end_a)
        wb = window(b, start_b, end_b) if b else analytics.previous_window(wa, like_for_like)
        return analytics.compare(wa, wb, default_line(line_id), product_id, line_b=line_b)

    @app.get("/api/breakdown")
    def breakdown(by: str = "day", period: str = "last_7d", start: str | None = None, end: str | None = None,
                  line_id: int | None = None, product_id: int | None = None, batch_id: int | None = None,
                  shift: str | None = None) -> list[dict]:
        if by not in ("day", "shift", "line", "product", "batch"):
            raise HTTPException(422, "by must be one of day, shift, line, product, batch")
        return analytics.breakdown(by, window(period, start, end), line_id, product_id, batch_id, shift)

    @app.get("/api/trend")
    def trend(period: str = "today", start: str | None = None, end: str | None = None, line_id: int | None = None,
              product_id: int | None = None) -> list[dict]:
        return analytics.trend(window(period, start, end), line_id, product_id)

    @app.get("/api/rejections")
    def rejections(period: str = "today", start: str | None = None, end: str | None = None,
                   line_id: int | None = None, product_id: int | None = None) -> list[dict]:
        return analytics.rejection_reasons(window(period, start, end), line_id, product_id)

    @app.get("/api/units")
    def units(line_id: int | None = None, product_id: int | None = None, batch_id: int | None = None,
              status: str | None = None, q: str | None = None, period: str | None = None, start: str | None = None,
              end: str | None = None, limit: int = Query(100, le=500), offset: int = 0) -> dict:
        w = window(period, start, end) if period else None
        return analytics.units(line_id, product_id, batch_id, status, q, w, limit, offset)

    @app.get("/api/units/{code}")
    def unit(code: str) -> dict:
        j = analytics.journey(code)
        if not j:
            raise HTTPException(404, f"no unit with code {code}")
        return j

    @app.get("/api/alerts")
    def list_alerts(status: str | None = None, limit: int = Query(100, le=500)) -> list[dict]:
        return store.list_alerts(status, limit)

    @app.post("/api/alerts/{aid}/ack", dependencies=[Depends(admin)])
    def ack_alert(aid: int) -> dict:
        return store.ack_alert(aid)

    @app.post("/api/alerts/evaluate", dependencies=[Depends(admin)])
    def evaluate_alerts() -> dict:
        return alerts.evaluate()

    @app.post("/api/assistant")
    def ask(body: QuestionIn) -> dict:
        return assistant.ask(body.question, body.line_id)

    @app.get("/api/assistant/examples")
    def examples() -> list[str]:
        from .assistant import EXAMPLES
        return EXAMPLES

    # ---- cameras -------------------------------------------------------------------
    def _cam_view(c: dict) -> dict:
        c = dict(c)
        c["source"] = redact(c["source"])
        c["status"] = cameras.status(c["id"])
        c["stations"] = [{"id": s["id"], "name": s["name"], "zone": s["zone"]} for s in store.list_stations() if s["camera_id"] == c["id"]]
        return c

    @app.get("/api/cameras")
    def list_cameras() -> list[dict]:
        return [_cam_view(c) for c in store.list_cameras()]

    @app.post("/api/cameras", dependencies=[Depends(admin)])
    def add_camera(body: CameraIn) -> dict:
        cam = store.add_camera(**body.model_dump())
        if cam["enabled"] and start_background:
            cameras.start(cam["id"])          # an unreachable source then shows its error straight away
        return _cam_view(cam)

    @app.patch("/api/cameras/{cid}", dependencies=[Depends(admin)])
    def patch_camera(cid: int, body: CameraPatch) -> dict:
        cam = store.update_camera(cid, **body.model_dump(exclude_none=True))
        if cameras.get(cid):                       # settings changed: restart with the new ones
            cameras.stop(cid)
            cameras.start(cid)
        return _cam_view(cam)

    @app.delete("/api/cameras/{cid}", dependencies=[Depends(admin)])
    def delete_camera(cid: int) -> dict:
        cameras.stop(cid)
        store.delete_camera(cid)
        return {"ok": True}

    @app.post("/api/cameras/{cid}/start", dependencies=[Depends(admin)])
    def start_camera(cid: int) -> dict:
        if not store.get_camera(cid):
            raise HTTPException(404, "no such camera")
        cameras.start(cid)
        return _cam_view(store.get_camera(cid))

    @app.post("/api/cameras/{cid}/stop", dependencies=[Depends(admin)])
    def stop_camera(cid: int) -> dict:
        cameras.stop(cid)
        return _cam_view(store.get_camera(cid))

    @app.post("/api/cameras/{cid}/recalibrate", dependencies=[Depends(admin)])
    def recalibrate(cid: int) -> dict:
        r = cameras.get(cid)
        if not r:
            raise HTTPException(409, "the camera isn't running")
        r.recalibrate()
        return {"ok": True, "note": "learning the empty scene again - keep the line clear for a few seconds"}

    @app.get("/api/cameras/{cid}/frame.jpg")
    def camera_frame(cid: int, annotated: bool = False) -> Response:
        cam = store.get_camera(cid)
        if not cam:
            raise HTTPException(404, "no such camera")
        r = cameras.get(cid)
        jpg = (r.latest_jpeg if annotated else r.raw_jpeg) if r else None
        if jpg is None:
            jpg = grab_frame(cam["source"])
        if jpg is None:
            raise HTTPException(503, "can't get a picture from this camera")
        return Response(jpg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/cameras/{cid}/live.mjpg")
    def camera_live(cid: int) -> StreamingResponse:
        r = cameras.get(cid)
        if not r:
            raise HTTPException(409, "the camera isn't running")

        def gen():
            last = None
            while True:
                jpg = r.latest_jpeg
                if jpg is not None and jpg is not last:
                    last = jpg
                    yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n"
                time.sleep(0.1)
                if not (r.thread and r.thread.is_alive()):
                    break
        return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")

    # ---- dashboard ---------------------------------------------------------------------
    if WEB.is_dir():
        app.mount("/static", StaticFiles(directory=WEB), name="static")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})

    return app


def main() -> None:
    import uvicorn
    s = Settings.load()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if s.host not in ("127.0.0.1", "localhost", "::1") and not s.api_key:
        log.warning("Listening on %s without MPI_API_KEY: other machines can read production data and "
                    "cannot ingest events until a key is set.", s.host)
    uvicorn.run(create_app(s), host=s.host, port=s.port, log_level="info")


if __name__ == "__main__":
    main()
