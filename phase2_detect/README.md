# Reality Connector — Phase 2: Person Detection + Tracking

Adds the AI layer on top of Phase 1: each frame from `CameraStream` goes through
**Ultralytics YOLO11** (person detection) and **BoT-SORT** (tracking), and the
viewer draws bounding boxes with persistent **track IDs**.

`phase1_camera` is untouched — Phase 2 imports `CameraStream` and reuses the same `.env`.

```
Camera → CameraStream → frame → YOLO11 detect → BoT-SORT track → box + ID → overlay
```

---

## Setup

Phase 1's venv is reused. Install PyTorch (**CUDA build** for the GTX 1660) then Ultralytics:

```bash
.venv\Scripts\python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
```
```bash
.venv\Scripts\python.exe -m pip install -r phase2_detect/requirements.txt
```

Verify the GPU is visible:
```bash
.venv\Scripts\python.exe -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```
Should print `True NVIDIA GeForce GTX 1660`. If `False`, it fell back to the CPU build — reinstall torch with the `--index-url` above.

The model file (`yolo11n.pt`, ~5 MB) auto-downloads on first run.

## Config — add to `phase1_camera/.env`

| Key | Default | Meaning |
|---|---|---|
| `YOLO_MODEL` | `yolo11n.pt` | `yolo11n` (fastest) · `yolo11s` (more accurate) · `yolo11m` … |
| `YOLO_DEVICE` | *(auto)* | blank = CUDA if available; `cpu`; `0` for GPU 0 |
| `YOLO_CONF` | `0.35` | min detection confidence |
| `YOLO_IOU` | `0.5` | NMS IoU threshold |
| `YOLO_IMGSZ` | `640` | inference resolution (lower = faster) |
| `YOLO_CLASSES` | `0` | COCO classes to keep — `0` = person only |
| `TRACKER` | `botsort.yaml` | `botsort.yaml` or `bytetrack.yaml` |
| `PROCESS_EVERY_N` | `1` | run AI every Nth frame — raise to `2`/`3` if `ai_fps` < stream fps |
| `YOLO_HALF` | `false` | FP16 — leave off on GTX 16-series (no tensor cores) |

Camera keys (`RTSP_URL` / `CAMERA_IP` / `CAMERA_SOURCE` …) are the same as Phase 1.

## Run

```bash
.venv\Scripts\python.exe -m phase2_detect.app
```
```bash
.venv\Scripts\python.exe -m phase2_detect.app --no-display --duration 600      # headless soak
```
```bash
.venv\Scripts\python.exe -m phase2_detect.app --save phase2_detect/logs/demo.mp4   # record annotated video
```

`q` / `Esc` quits. The overlay shows resolution, display FPS, AI FPS + inference ms,
current person count, and the number of unique track IDs seen.

## What "working" looks like (maps to acceptance doc §5.1–5.2)

- Every visible person gets a box; `person` confidence ≥ ~0.35
- Each person keeps the **same ID** while continuously visible
- Walking out and back in gets a **new ID** (cross-camera / re-ID is later)
- `ai_fps` keeps up with the stream (raise `PROCESS_EVERY_N` or lower `YOLO_IMGSZ` if not)

## Module layout

| File | Responsibility |
|---|---|
| `config.py` | `Phase2Config` — camera config (from Phase 1) + AI params from `.env` |
| `detector.py` | `PersonTracker` — wraps `YOLO.track()`, returns plain `Track` records, frame decimation |
| `draw.py` | box + ID + stats overlay |
| `app.py` | capture → track → draw → display / record loop, reconnect, metrics |

## Notes

- Detection and capture run in one loop (synchronous). If the GPU can't keep up,
  frames buffer — use `PROCESS_EVERY_N` or set `CAPTURE_BUFFER=1` in `.env`.
  A producer/consumer split is a Phase 7 (optimization) concern.
- `Track.track_id` is `None` for the first frame or two until BoT-SORT locks on.
- Ultralytics YOLO is **AGPL-3.0**; a commercial licence is needed for production
  (see the MVP Acceptance Criteria doc, Assumptions & Dependencies).
