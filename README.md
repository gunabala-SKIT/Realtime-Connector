# Reality Connector

Vision AI platform that turns camera infrastructure into structured business events.
See `Reality_Connector_MVP_Acceptance_Criteria_v0.1.docx` for measurable MVP targets.

## Status

| Phase | Scope | State |
|---|---|---|
| **Phase 1** | Hikvision DVR/NVR → Python → OpenCV live view, reconnect, logging, metrics | In progress — see [`phase1_camera/`](phase1_camera/README.md) |
| Phase 2 | YOLO person detection + BoT-SORT tracking | Not started |
| Phase 3+ | Face detection/recognition, event engine, rules, dashboard | Not started |

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
