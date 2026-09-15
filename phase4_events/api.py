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

  GET    /rules                        -> configured rules (Phase 6)
  POST   /rules                        -> create a rule
  PATCH  /rules/{id}                   -> update fields (e.g. {"enabled": false})
  DELETE /rules/{id}
  GET    /alerts        filters: status, priority, limit, offset
  GET    /alerts/{id}
  POST   /alerts/{id}   {"status"?, "assigned_to"?, "comment"?} -> ack/close/assign/annotate

  WS   /ws/events                      -> pushes new events (with alert info) as JSON, ~1 s
  WS   /ws/alerts                      -> pushes new alerts as JSON, ~1 s
"""
from __future__ import annotations

import asyncio
import time
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
