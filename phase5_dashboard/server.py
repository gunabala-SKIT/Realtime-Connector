"""Reality Connector - Phase 5 dashboard server.

One process: runs the vision pipeline in a background thread AND serves

  /                     the dashboard SPA (phase5_dashboard/web/)
  /live.mjpg            annotated live video (multipart/x-mixed-replace)
  /live/frame.jpg       one current frame as a still JPEG (background for the zone editor)
  /live/status          pipeline status JSON (fps, counts, uptime)
  /api/*                the Phase 4 events API (events, stats, snapshots, alerts, rules, zones)

    python -m phase5_dashboard            # then open http://127.0.0.1:8000
"""
from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from phase1_camera.logger import get_logger
from phase4_events.api import app as events_app
from phase4_events.pipeline import VisionPipeline
from phase6_rules.config import Phase6Config
from phase6_rules.rules import RuleEngine
from phase7_zones.index import ZoneIndex

WEB_DIR = Path(__file__).resolve().parent / "web"

cfg6 = Phase6Config.load()
cfg = cfg6.phase4
log = get_logger("reality_connector.phase5", cfg.phase3.log_level, str(cfg.phase3.log_dir))
pipeline = VisionPipeline(cfg, log)
rule_engine = RuleEngine(cfg6, pipeline.store, log)
pipeline.event_hooks.append(rule_engine.process_events)
if not cfg6.email_configured:
    log.info("SMTP not configured - alert emails disabled (set SMTP_HOST + ALERT_EMAIL_TO to enable).")

zone_index = ZoneIndex(pipeline.store)
pipeline.engine.zone_resolver = zone_index.resolve


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        await asyncio.to_thread(pipeline.start)   # model warmup is blocking
    except Exception as e:  # noqa: BLE001
        log.error("Pipeline failed to start: %s", e)
    yield
    pipeline.stop()


app = FastAPI(title="Reality Connector - Dashboard", version="0.1.0", lifespan=lifespan)


@app.get("/live.mjpg")
def live_mjpg() -> StreamingResponse:
    return StreamingResponse(
        pipeline.mjpeg(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/live/frame.jpg")
def live_frame() -> Response:
    jpg = pipeline.latest_jpeg()
    if jpg is None:
        raise HTTPException(503, "no frame yet - still warming up")
    return Response(content=jpg, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@app.get("/live/status")
def live_status() -> dict:
    return pipeline.status()


app.mount("/api", events_app)
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")


def main() -> int:
    import uvicorn

    log.info("Dashboard  ->  http://%s:%d", cfg.api_host, cfg.api_port)
    uvicorn.run(app, host=cfg.api_host, port=cfg.api_port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
