"""Process entrypoint.

    python -m garment_cv                              # start the server (host/port from config)

Wires together: settings -> camera config -> CameraRunner -> zones -> activity engine -> database
-> (optional) ObjectDetector -> FastAPI app -> uvicorn. The detector attaches to the camera purely
through `CameraRunner.frame_hooks` - see `backend/cameras/runner.py`'s module docstring - so
nothing about the camera module changed to add it; zone lookups reach the detector only through
the small `_ZoneLookup` protocol it defines (`backend/zones/resolver.py`), and completed
activities reach the database only through `ActivityEngine`'s `on_complete` callback
(`backend/activities/engine.py`) - `activities/` never imports `events/` or `database/` itself.
"""
from __future__ import annotations

from fastapi import FastAPI

from .activities.config import ActivityConfig
from .activities.engine import ActivityEngine, CompletedActivity
from .api.routes import create_app
from .cameras.config import CameraConfig
from .cameras.runner import CameraRunner
from .config import AppSettings
from .database.config import db_file_path
from .database.db import Database
from .detection.config import DetectionConfig
from .detection.detector import ObjectDetector
from .events.store import EventStore
from .logger import get_logger
from .tracking.config import TrackingConfig
from .workers.store import WorkerStore
from .zones.config import zones_file_path
from .zones.resolver import ZoneResolver
from .zones.store import ZoneStore


def _record_completed_activity(event_store: EventStore, worker_store: WorkerStore,
                               completed: CompletedActivity) -> None:
    """Phase 6: turns one finished activity into a persisted production event (spec section 12),
    resolving the track's worker name through the manual-assignment store (spec section 13) so
    every event has a readable `worker` even when nobody has assigned one yet."""
    event_store.record(camera_id=completed.camera_id, track_id=completed.track_id,
                       worker=worker_store.name_for(completed.track_id), stage=completed.activity,
                       zone=completed.zone, start_ts=completed.start_ts, end_ts=completed.end_ts,
                       duration_s=completed.duration_s, confidence=completed.confidence)


def build(settings: AppSettings | None = None, start_camera: bool = True) -> tuple[
        FastAPI, CameraRunner, ObjectDetector, ZoneStore, ActivityEngine, EventStore, WorkerStore]:
    settings = settings or AppSettings.load()
    log = get_logger(level=settings.log_level, log_dir=str(settings.log_dir))
    cam_cfg = CameraConfig.load()

    if not cam_cfg.source:
        log.error("No camera configured. Set GARMENT_CAMERA_SOURCE in garment_cv/configs/.env "
                  "(a webcam index like '0', an rtsp:// URL, or a video file path for testing).")

    runner = CameraRunner(cam_cfg, log)
    zone_store = ZoneStore(zones_file_path())
    db = Database(db_file_path())
    event_store = EventStore(db)
    worker_store = WorkerStore(db)
    activity_engine = ActivityEngine(ActivityConfig.load(),
                                     on_complete=lambda c: _record_completed_activity(event_store, worker_store, c))
    detector = ObjectDetector(DetectionConfig.load(), log, tracking=TrackingConfig.load(),
                              zones=ZoneResolver(zone_store), activities=activity_engine)
    app = create_app(runner, detector=detector, zone_store=zone_store, start_camera=start_camera,
                     activity_engine=activity_engine, event_store=event_store, worker_store=worker_store)
    return app, runner, detector, zone_store, activity_engine, event_store, worker_store


def run() -> None:
    import uvicorn
    settings = AppSettings.load()
    app, _runner, _detector, _zones, _activities, _events, _workers = build(settings)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level=settings.log_level.lower())


if __name__ == "__main__":
    run()
