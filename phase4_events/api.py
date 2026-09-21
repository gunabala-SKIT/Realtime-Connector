"""Read-only-ish backend for events (BRD FR-012/015/016/017).

    python -m phase4_events.api          # or: uvicorn phase4_events.api:app

Endpoints
  GET  /healthz
  GET  /stats?window=today|24h|7d
  GET  /events         filters: since,until (epoch or ISO), camera_id, identity_status,
                                 employee_code, alert_status, limit, offset, order
                        each event is enriched with alert_id / alert_priority if one exists
  GET  /events/{id}
  GET  /events/{id}/snapshot           -> image/jpeg
  POST /events/{id}/alert  {"status": "none|open|ack|closed"}   (quick-glance flag on the event)
  GET  /employees                      -> enrolled gallery

  GET    /rules                        -> configured rules (Phase 6), each may set "zone"
  POST   /rules                        -> create a rule
  PATCH  /rules/{id}                   -> update fields (e.g. {"enabled": false})
  DELETE /rules/{id}
  GET    /alerts        filters: status, priority, limit, offset
  GET    /alerts/{id}
  POST   /alerts/{id}   {"status"?, "assigned_to"?, "comment"?} -> ack/close/assign/annotate

  GET    /zones?camera_id=             -> configured zones (Phase 7)
  POST   /zones   {camera_id, name, points: [[x,y]..], color?, capacity?, queue_cooldown_s?}
  PATCH  /zones/{id}
  DELETE /zones/{id}
  GET    /zones/summary                -> latest queue check per zone (Phase 9), for a dashboard tile
  GET    /queue-checks?zone_id=&limit=
  GET    /queue-alerts?status=
  POST   /queue-alerts/{id}   {"status"?, "assigned_to"?, "comment"?}

  GET    /count-lines?camera_id=        -> configured entry/exit lines (Phase 10)
  POST   /count-lines   {camera_id, name, points: [[x,y],[x,y]], positive_is_in?, color?}
  PATCH  /count-lines/{id}
  DELETE /count-lines/{id}
  GET    /count-lines/summary           -> each line + {entries, exits, net} since its reset
  POST   /count-lines/{id}/reset        -> zero current totals, keeps crossing history
  GET    /crossings?line_id=&limit=     -> raw crossing log

  GET    /shelves?camera_id=           -> configured shelves (Phase 8)
  POST   /shelves    {camera_id, name, points, capacity, product_name?, low_stock_threshold?, ...}
  PATCH  /shelves/{id}
  DELETE /shelves/{id}
  GET    /shelves/summary              -> latest check per shelf, for a dashboard tile
  GET    /shelf-checks?shelf_id=&limit=
  GET    /shelf-checks/{id}/snapshot   -> image/jpeg
  GET    /shelf-alerts?status=
  POST   /shelf-alerts/{id}   {"status"?, "assigned_to"?, "comment"?}

  GET    /camera-roles                 -> {roles: {camera_id: role}, known_camera_ids: [...]} (Phase 11)
  POST   /camera-roles   {camera_id, role: "entry"|"exit"|"both"|"internal"}
  DELETE /camera-roles/{camera_id}
  GET    /attendance?date=YYYY-MM-DD   -> per-employee entry/exit across every camera, default today
  GET    /track/{employee_code}?date=&limit=  -> current camera + recent path (Phase 12), default today

  WS   /ws/events                      -> pushes new events (with alert info + zone) as JSON, ~1 s
  WS   /ws/alerts                      -> pushes new alerts as JSON, ~1 s
"""
from __future__ import annotations

import asyncio
import time
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from .config import Phase4Config
from .store import EventStore

cfg = Phase4Config.load()
store = EventStore(cfg.events_db)

app = FastAPI(title="Reality Connector - Events API", version="0.1.0")


def _parse_ts(v: str | None) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except ValueError:
        pass
    from datetime import datetime
    return datetime.fromisoformat(v).timestamp()


def _window_start(window: str) -> float:
    now = time.time()
    if window == "today":
        t = time.localtime(now)
        return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
    return now - {"24h": 86400, "7d": 7 * 86400, "1h": 3600}.get(window, 86400)


def _enrich(rows: list) -> list[dict]:
    """Attach alert_id / alert_priority to each event dict, for the dashboard."""
    alerts = store.get_alerts_for_events([e.id for e in rows])
    out = []
    for e in rows:
        d = e.to_dict()
        a = alerts.get(e.id)
        d["alert_id"] = a.id if a else None
        d["alert_priority"] = a.priority if a else None
        out.append(d)
    return out


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "db": str(cfg.events_db), "max_event_id": store.max_id(),
            "gallery": len(store.list_employees())}


@app.get("/stats")
def stats(window: str = Query("today", pattern="^(today|1h|24h|7d)$")) -> dict:
    return {"window": window, **store.stats(_window_start(window))}


@app.get("/events")
def events(
    since: str | None = None, until: str | None = None,
    camera_id: str | None = None, identity_status: str | None = None,
    employee_code: str | None = None, alert_status: str | None = None,
    limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
    order: str = Query("desc", pattern="^(asc|desc)$"),
) -> dict:
    rows = store.query(
        since=_parse_ts(since), until=_parse_ts(until), camera_id=camera_id,
        identity_status=identity_status, employee_code=employee_code,
        alert_status=alert_status, limit=limit, offset=offset, order=order,
    )
    return {"count": len(rows), "events": _enrich(rows)}


@app.get("/events/{event_id}")
def event(event_id: int) -> dict:
    ev = store.get(event_id)
    if not ev:
        raise HTTPException(404, "event not found")
    return _enrich([ev])[0]


@app.get("/events/{event_id}/snapshot")
def snapshot(event_id: int):
    ev = store.get(event_id)
    if not ev or not ev.snapshot:
        raise HTTPException(404, "no snapshot for this event")
    path = Path(cfg.snapshot_dir) / ev.snapshot
    if not path.is_file():
        raise HTTPException(404, "snapshot file missing")
    return FileResponse(str(path), media_type="image/jpeg")


class AlertUpdate(BaseModel):
    status: str


@app.post("/events/{event_id}/alert")
def set_alert(event_id: int, body: AlertUpdate) -> dict:
    if body.status not in ("none", "open", "ack", "closed"):
        raise HTTPException(422, "status must be none|open|ack|closed")
    if not store.set_alert_status(event_id, body.status):
        raise HTTPException(404, "event not found")
    return {"id": event_id, "alert_status": body.status}


@app.get("/employees")
def employees() -> dict:
    rows = store.list_employees()
    return {"count": len(rows), "employees": rows}


# ---------------------------------------------------------------------- rules
class RuleIn(BaseModel):
    name: str
    identity_status: str | None = None   # None = any; else RECOGNIZED|UNKNOWN|UNVERIFIED
    camera_id: str | None = None         # None = any
    time_start: str | None = None        # "HH:MM"
    time_end: str | None = None          # "HH:MM"
    days_of_week: str | None = None      # "0,1,2,3,4,5,6" Mon=0, or None = every day
    zone: str | None = None              # zone name (Phase 7), None = any
    priority: str = "high"               # low | medium | high | critical
    notify_email: bool = False
    cooldown_s: float = 120
    enabled: bool = True


class RulePatch(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    identity_status: str | None = None
    camera_id: str | None = None
    time_start: str | None = None
    time_end: str | None = None
    days_of_week: str | None = None
    zone: str | None = None
    priority: str | None = None
    notify_email: bool | None = None
    cooldown_s: float | None = None


_PRIORITIES = ("low", "medium", "high", "critical")
_STATUSES = ("RECOGNIZED", "UNKNOWN", "UNVERIFIED")


@app.get("/rules")
def list_rules() -> dict:
    rows = store.list_rules()
    return {"count": len(rows), "rules": [r.to_dict() for r in rows]}


@app.post("/rules")
def create_rule(body: RuleIn) -> dict:
    if body.priority not in _PRIORITIES:
        raise HTTPException(422, f"priority must be one of {_PRIORITIES}")
    if body.identity_status and body.identity_status not in _STATUSES:
        raise HTTPException(422, f"identity_status must be one of {_STATUSES} or null")
    rule = store.add_rule(**body.model_dump())
    return rule.to_dict()


@app.patch("/rules/{rule_id}")
def patch_rule(rule_id: int, body: RulePatch) -> dict:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "priority" in fields and fields["priority"] not in _PRIORITIES:
        raise HTTPException(422, f"priority must be one of {_PRIORITIES}")
    rule = store.update_rule(rule_id, **fields)
    if not rule:
        raise HTTPException(404, "rule not found")
    return rule.to_dict()


@app.delete("/rules/{rule_id}")
def delete_rule(rule_id: int) -> dict:
    if not store.delete_rule(rule_id):
        raise HTTPException(404, "rule not found")
    return {"deleted": rule_id}


# ---------------------------------------------------------------------- zones
class ZoneIn(BaseModel):
    camera_id: str
    name: str
    points: list[list[float]]     # >= 3 [x, y] pairs, each 0..1
    color: str = "#3ccfd7"
    enabled: bool = True
    capacity: int | None = None       # Phase 9: set -> this zone is also a queue/crowd zone
    queue_cooldown_s: float = 60.0


class ZonePatch(BaseModel):
    name: str | None = None
    points: list[list[float]] | None = None
    color: str | None = None
    enabled: bool | None = None
    capacity: int | None = None
    queue_cooldown_s: float | None = None


@app.get("/zones")
def list_zones(camera_id: str | None = None) -> dict:
    rows = store.list_zones(camera_id=camera_id)
    return {"count": len(rows), "zones": [z.to_dict() for z in rows]}


@app.get("/zones/summary")
def zones_summary() -> dict:
    """Every zone plus its latest queue-occupancy check, if it's capacity-
    configured - what the dashboard's queue occupancy display uses."""
    zones = store.list_zones()
    latest = store.latest_queue_checks()
    out = []
    for z in zones:
        chk = latest.get(z.id)
        out.append({**z.to_dict(), "last_check": chk.to_dict() if chk else None})
    return {"count": len(out), "zones": out}


@app.post("/zones")
def create_zone(body: ZoneIn) -> dict:
    if len(body.points) < 3:
        raise HTTPException(422, "a zone needs at least 3 points")
    if body.capacity is not None and body.capacity < 1:
        raise HTTPException(422, "capacity must be >= 1")
    zone = store.add_zone(**body.model_dump())
    return zone.to_dict()


@app.patch("/zones/{zone_id}")
def patch_zone(zone_id: int, body: ZonePatch) -> dict:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "points" in fields and len(fields["points"]) < 3:
        raise HTTPException(422, "a zone needs at least 3 points")
    zone = store.update_zone(zone_id, **fields)
    if not zone:
        raise HTTPException(404, "zone not found")
    return zone.to_dict()


@app.delete("/zones/{zone_id}")
def delete_zone(zone_id: int) -> dict:
    if not store.delete_zone(zone_id):
        raise HTTPException(404, "zone not found")
    return {"deleted": zone_id}


@app.get("/queue-checks")
def list_queue_checks(zone_id: int | None = None, limit: int = Query(100, ge=1, le=1000)) -> dict:
    rows = store.list_queue_checks(zone_id=zone_id, limit=limit)
    return {"count": len(rows), "checks": [c.to_dict() for c in rows]}


@app.get("/queue-alerts")
def list_queue_alerts(status: str | None = None, limit: int = Query(100, ge=1, le=1000)) -> dict:
    rows = store.list_queue_alerts(status=status, limit=limit)
    return {"count": len(rows), "alerts": [a.to_dict() for a in rows]}


class QueueAlertPatch(BaseModel):
    status: str | None = None
    assigned_to: str | None = None
    comment: str | None = None


@app.post("/queue-alerts/{alert_id}")
def update_queue_alert(alert_id: int, body: QueueAlertPatch) -> dict:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "status" in fields and fields["status"] not in ("open", "ack", "closed"):
        raise HTTPException(422, "status must be open|ack|closed")
    a = store.update_queue_alert(alert_id, **fields)
    if not a:
        raise HTTPException(404, "queue alert not found")
    return a.to_dict()


# --------------------------------------------------------------- count lines
class CountLineIn(BaseModel):
    camera_id: str
    name: str
    points: list[list[float]]      # exactly 2 [x, y] pairs, each 0..1
    positive_is_in: bool = True
    color: str = "#e0a53a"
    enabled: bool = True


class CountLinePatch(BaseModel):
    name: str | None = None
    points: list[list[float]] | None = None
    positive_is_in: bool | None = None
    color: str | None = None
    enabled: bool | None = None


@app.get("/count-lines")
def list_count_lines(camera_id: str | None = None) -> dict:
    rows = store.list_count_lines(camera_id=camera_id)
    return {"count": len(rows), "lines": [l.to_dict() for l in rows]}


@app.get("/count-lines/summary")
def count_lines_summary() -> dict:
    lines = store.list_count_lines()
    out = []
    for ln in lines:
        counts = store.crossing_counts(ln.id)
        entries, exits = counts.get("in", 0), counts.get("out", 0)
        out.append({**ln.to_dict(), "entries": entries, "exits": exits, "net": entries - exits})
    return {"count": len(out), "lines": out}


@app.post("/count-lines")
def create_count_line(body: CountLineIn) -> dict:
    if len(body.points) != 2:
        raise HTTPException(422, "a counting line needs exactly 2 points")
    line = store.add_count_line(**body.model_dump())
    return line.to_dict()


@app.patch("/count-lines/{line_id}")
def patch_count_line(line_id: int, body: CountLinePatch) -> dict:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "points" in fields and len(fields["points"]) != 2:
        raise HTTPException(422, "a counting line needs exactly 2 points")
    line = store.update_count_line(line_id, **fields)
    if not line:
        raise HTTPException(404, "count line not found")
    return line.to_dict()


@app.delete("/count-lines/{line_id}")
def delete_count_line(line_id: int) -> dict:
    if not store.delete_count_line(line_id):
        raise HTTPException(404, "count line not found")
    return {"deleted": line_id}


@app.post("/count-lines/{line_id}/reset")
def reset_count_line(line_id: int) -> dict:
    line = store.reset_count_line(line_id)
    if not line:
        raise HTTPException(404, "count line not found")
    return line.to_dict()


@app.get("/crossings")
def list_crossings(line_id: int | None = None, limit: int = Query(100, ge=1, le=1000)) -> dict:
    rows = store.list_crossings(line_id=line_id, limit=limit)
    return {"count": len(rows), "crossings": [c.to_dict() for c in rows]}


# ----------------------------------------------------------- camera roles
class CameraRoleIn(BaseModel):
    camera_id: str
    role: str


@app.get("/camera-roles")
def list_camera_roles() -> dict:
    return {"roles": store.camera_roles(), "known_camera_ids": store.known_camera_ids()}


@app.post("/camera-roles")
def set_camera_role(body: CameraRoleIn) -> dict:
    if body.role not in store.VALID_CAMERA_ROLES:
        raise HTTPException(422, f"role must be one of {store.VALID_CAMERA_ROLES}")
    return store.set_camera_role(body.camera_id, body.role)


@app.delete("/camera-roles/{camera_id}")
def delete_camera_role(camera_id: str) -> dict:
    if not store.delete_camera_role(camera_id):
        raise HTTPException(404, "camera_id has no role set")
    return {"deleted": camera_id}


# ------------------------------------------------------------- attendance
@app.get("/attendance")
def attendance(date_str: str | None = Query(None, alias="date")) -> dict:
    date_str = date_str or date.today().isoformat()
    try:
        rows = store.attendance_for_date(date_str)
    except ValueError:
        raise HTTPException(422, "date must be YYYY-MM-DD")
    return {"date": date_str, "count": len(rows), "attendance": rows}


# --------------------------------------------------------------- live tracking
@app.get("/track/{employee_code}")
def track_employee(employee_code: str, date_str: str | None = Query(None, alias="date"),
                    limit: int = Query(20, ge=1, le=200)) -> dict:
    date_str = date_str or date.today().isoformat()
    try:
        path = store.track_employee(employee_code, date_str=date_str, limit=limit)
    except ValueError:
        raise HTTPException(422, "date must be YYYY-MM-DD")
    current = path[0] if path else None
    return {
        "employee_code": employee_code,
        "date": date_str,
        "current_camera": current["camera_id"] if current else None,
        "last_seen_ts": current["ts"] if current else None,
        "count": len(path),
        "path": path,
    }


# --------------------------------------------------------------------- shelves
_WHOLE_FRAME = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]


class ShelfIn(BaseModel):
    camera_id: str
    name: str
    points: list[list[float]] | None = None   # >= 3 [x,y] pairs 0..1; default = whole frame
    capacity: int
    product_name: str | None = None
    low_stock_threshold: float = 0.7  # alert when detected/capacity <= this
    notify_email: bool = False
    cooldown_s: float = 1800
    enabled: bool = True


class ShelfPatch(BaseModel):
    name: str | None = None
    points: list[list[float]] | None = None
    capacity: int | None = None
    product_name: str | None = None
    low_stock_threshold: float | None = None
    notify_email: bool | None = None
    cooldown_s: float | None = None
    enabled: bool | None = None


@app.get("/shelves")
def list_shelves(camera_id: str | None = None) -> dict:
    rows = store.list_shelves(camera_id=camera_id)
    return {"count": len(rows), "shelves": [s.to_dict() for s in rows]}


@app.get("/shelves/summary")
def shelves_summary() -> dict:
    shelves = store.list_shelves()
    latest = store.latest_shelf_checks()
    out = []
    for sh in shelves:
        chk = latest.get(sh.id)
        out.append({
            **sh.to_dict(),
            "last_check": chk.to_dict() if chk else None,
        })
    return {"count": len(out), "shelves": out}


@app.post("/shelves")
def create_shelf(body: ShelfIn) -> dict:
    points = body.points or _WHOLE_FRAME
    if len(points) < 3:
        raise HTTPException(422, "a shelf region needs at least 3 points")
    if body.capacity < 1:
        raise HTTPException(422, "capacity must be >= 1")
    fields = body.model_dump()
    fields["points"] = points
    shelf = store.add_shelf(**fields)
    return shelf.to_dict()


@app.patch("/shelves/{shelf_id}")
def patch_shelf(shelf_id: int, body: ShelfPatch) -> dict:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    shelf = store.update_shelf(shelf_id, **fields)
    if not shelf:
        raise HTTPException(404, "shelf not found")
    return shelf.to_dict()


@app.delete("/shelves/{shelf_id}")
def delete_shelf(shelf_id: int) -> dict:
    if not store.delete_shelf(shelf_id):
        raise HTTPException(404, "shelf not found")
    return {"deleted": shelf_id}


@app.get("/shelf-checks")
def list_shelf_checks(shelf_id: int | None = None, limit: int = Query(100, ge=1, le=1000)) -> dict:
    rows = store.list_shelf_checks(shelf_id=shelf_id, limit=limit)
    return {"count": len(rows), "checks": [c.to_dict() for c in rows]}


@app.get("/shelf-checks/{check_id}/snapshot")
def shelf_check_snapshot(check_id: int):
    rows = store.list_shelf_checks(limit=1000)
    check = next((c for c in rows if c.id == check_id), None)
    if not check or not check.snapshot:
        raise HTTPException(404, "no snapshot for this check")
    path = Path(cfg.snapshot_dir) / check.snapshot
    if not path.is_file():
        raise HTTPException(404, "snapshot file missing")
    return FileResponse(str(path), media_type="image/jpeg")


@app.get("/shelf-alerts")
def list_shelf_alerts(status: str | None = None, limit: int = Query(100, ge=1, le=1000)) -> dict:
    rows = store.list_shelf_alerts(status=status, limit=limit)
    return {"count": len(rows), "alerts": [a.to_dict() for a in rows]}


class ShelfAlertPatch(BaseModel):
    status: str | None = None
    assigned_to: str | None = None
    comment: str | None = None


@app.post("/shelf-alerts/{alert_id}")
def update_shelf_alert(alert_id: int, body: ShelfAlertPatch) -> dict:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "status" in fields and fields["status"] not in ("open", "ack", "closed"):
        raise HTTPException(422, "status must be open|ack|closed")
    a = store.update_shelf_alert(alert_id, **fields)
    if not a:
        raise HTTPException(404, "shelf alert not found")
    return a.to_dict()


# --------------------------------------------------------------------- alerts
class AlertPatch(BaseModel):
    status: str | None = None
    assigned_to: str | None = None
    comment: str | None = None


@app.get("/alerts")
def list_alerts(
    status: str | None = None, priority: str | None = None,
    limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
) -> dict:
    rows = store.list_alerts(status=status, priority=priority, limit=limit, offset=offset)
    return {"count": len(rows), "alerts": [a.to_dict() for a in rows]}


@app.get("/alerts/{alert_id}")
def get_alert(alert_id: int) -> dict:
    a = store.get_alert(alert_id)
    if not a:
        raise HTTPException(404, "alert not found")
    return a.to_dict()


@app.post("/alerts/{alert_id}")
def update_alert(alert_id: int, body: AlertPatch) -> dict:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "status" in fields and fields["status"] not in ("open", "ack", "closed"):
        raise HTTPException(422, "status must be open|ack|closed")
    a = store.update_alert(alert_id, **fields)
    if not a:
        raise HTTPException(404, "alert not found")
    return a.to_dict()


@app.websocket("/ws/events")
async def ws_events(ws: WebSocket) -> None:
    await ws.accept()
    last_id = store.max_id()
    await ws.send_json({"type": "hello", "last_event_id": last_id})
    try:
        while True:
            await asyncio.sleep(1.0)
            rows = await asyncio.to_thread(store.query, after_id=last_id, order="asc", limit=200)
            if rows:
                enriched = await asyncio.to_thread(_enrich, rows)
                for d in enriched:
                    await ws.send_json({"type": "event", "event": d})
                last_id = rows[-1].id
    except WebSocketDisconnect:
        pass


@app.websocket("/ws/alerts")
async def ws_alerts(ws: WebSocket) -> None:
    await ws.accept()
    last_id = max((a.id for a in store.list_alerts(limit=1)), default=0)
    await ws.send_json({"type": "hello", "last_alert_id": last_id})
    try:
        while True:
            await asyncio.sleep(1.0)
            rows = await asyncio.to_thread(store.list_alerts, limit=200)
            new = sorted((a for a in rows if a.id > last_id), key=lambda a: a.id)
            for a in new:
                await ws.send_json({"type": "alert", "alert": a.to_dict()})
                last_id = a.id
    except WebSocketDisconnect:
        pass


@app.exception_handler(Exception)
async def _unhandled(_req, exc: Exception):  # keep errors as JSON
    return JSONResponse(status_code=500, content={"error": str(exc)})


def main() -> int:
    import uvicorn
    uvicorn.run("phase4_events.api:app", host=cfg.api_host, port=cfg.api_port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
