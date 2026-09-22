# Reality Connector

Vision AI platform that turns camera infrastructure into structured business events.
See `Reality_Connector_MVP_Acceptance_Criteria_v0.1.docx` for measurable MVP targets.

## Status

| Phase | Scope | State |
|---|---|---|
| **Phase 1** | RTSP → OpenCV live view, reconnect, logging, metrics | Done — [`phase1_camera/`](phase1_camera/README.md). Validated against a phone RTSP feed (the site DVR's RTSP output is misconfigured — see memory). |
| **Phase 2** | YOLO11 person detection + BoT-SORT tracking, threaded low-latency capture | Done — [`phase2_detect/`](phase2_detect/README.md) |
| **Phase 3** | SCRFD face detection + ArcFace recognition + enrollment + RECOGNIZED/UNKNOWN/UNVERIFIED identity states | Done — [`phase3_identity/`](phase3_identity/README.md) |
| **Phase 4** | Deduplicated event engine + snapshots + FastAPI REST/WebSocket backend | Done — [`phase4_events/`](phase4_events/README.md) |
| **Phase 5** | Web dashboard (live video + events + stats + alerts) · ChromaDB vector store | Done — [`phase5_dashboard/`](phase5_dashboard/README.md) |
| **Phase 6** | Rule engine (identity/camera/time conditions) + alerts + email notifications | Done — [`phase6_rules/`](phase6_rules/README.md) |
| **Phase 7** | Zones — draw regions, tag events, add a zone condition to rules | Done — [`phase7_zones/`](phase7_zones/README.md) |
| **Phase 8** | Shelf / inventory monitoring — dedicated camera counts stock, alerts on low restock | Done — [`phase8_shelf/`](phase8_shelf/README.md) |
| **Phase 9** | Queue / crowd density monitoring — any zone can alert when over capacity | Done — [`phase9_queue/`](phase9_queue/README.md) |
| **Phase 10** | Footfall / entry-exit counting — draw a line, count people crossing it by track (not per-frame) | Done — [`phase10_footfall/`](phase10_footfall/README.md) |
| **Phase 11** | Cross-camera employee attendance — entry on one camera, exit on another, aggregated per employee per day | Done — [`phase11_attendance/`](phase11_attendance/README.md) |
| **Phase 12** | Live "select and follow" cross-camera tracking — pick an employee, see which camera they're on update live | Done — [`phase12_tracking/`](phase12_tracking/README.md) |
| **Phase 13** | Vehicle entry/exit tracking — generic vehicle detection + OCR license-plate reading, majority-vote confirmed | Done — [`phase13_vehicle/`](phase13_vehicle/README.md) |
| Phase 14+ | Multi-camera production hardening | Not started |

```bash
python -m phase5_dashboard      # everything, at http://127.0.0.1:8000
```

## Quick start (Phase 1)

```bash
py -m venv .venv
.venv\Scripts\activate
pip install -r phase1_camera/requirements.txt
copy phase1_camera\.env.example phase1_camera\.env   # then edit with your DVR details
python -m phase1_camera.check_network
python -m phase1_camera.app
```

Full instructions: [`phase1_camera/README.md`](phase1_camera/README.md).
