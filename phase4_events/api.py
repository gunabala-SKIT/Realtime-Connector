"""Read-only-ish backend for events (BRD FR-012/015/016/017).

    python -m phase4_events.api          # or: uvicorn phase4_events.api:app

Endpoints
  GET  /healthz
  GET  /stats?window=today|24h|7d
  GET  /events         filters: since,until (epoch or ISO), camera_id, identity_status,
                                 employee_code, alert_status, limit, offset, order
  GET  /events/{id}
  GET  /events/{id}/snapshot          -> image/jpeg
  POST /events/{id}/alert  {"status": "open|ack|closed"}   (Phase 7 will drive this)
  GET  /employees                     -> enrolled gallery
  WS   /ws/events                     -> pushes new events as JSON, ~1 s latency
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
    return {"count": len(rows), "events": [e.to_dict() for e in rows]}


@app.get("/events/{event_id}")
def event(event_id: int) -> dict:
    ev = store.get(event_id)
    if not ev:
        raise HTTPException(404, "event not found")
    return ev.to_dict()


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


@app.websocket("/ws/events")
async def ws_events(ws: WebSocket) -> None:
    await ws.accept()
    last_id = store.max_id()
    await ws.send_json({"type": "hello", "last_event_id": last_id})
    try:
        while True:
            await asyncio.sleep(1.0)
            rows = await asyncio.to_thread(store.query, after_id=last_id, order="asc", limit=200)
            for ev in rows:
                await ws.send_json({"type": "event", "event": ev.to_dict()})
                last_id = ev.id
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
