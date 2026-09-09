"""Reality Connector - Phase 5 dashboard server.

One process: runs the vision pipeline in a background thread AND serves

  /                     the dashboard SPA (phase5_dashboard/web/)
  /live.mjpg            annotated live video (multipart/x-mixed-replace)
  /live/status          pipeline status JSON (fps, counts, uptime)
  /api/*                the Phase 4 events API (events, stats, snapshots, alerts, /ws/events)

    python -m phase5_dashboard            # then open http://127.0.0.1:8000
"""
from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from phase1_camera.logger import get_logger
from phase4_events.api import app as events_app
from phase4_events.config import Phase4Config
from phase4_events.pipeline import VisionPipeline

WEB_DIR = Path(__file__).resolve().parent / "web"

cfg = Phase4Config.load()
log = get_logger("reality_connector.phase5", cfg.phase3.log_level, str(cfg.phase3.log_dir))
pipeline = VisionPipeline(cfg, log)


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
