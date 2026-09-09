# Reality Connector — Phase 5: Web Dashboard

One command, one browser tab: **live annotated video + event feed + stats + alert
handling**. Phase 5 also swaps the employee vector store from the Phase 3
SQLite + numpy matrix to **ChromaDB**.

```
python -m phase5_dashboard          →  http://127.0.0.1:8000
```

That single process:
- runs the full vision pipeline (Phases 2–4) in a background thread — writes
  deduplicated events + snapshots
- serves the dashboard SPA at `/`
- streams the annotated video at `/live.mjpg`
- mounts the Phase 4 events API under `/api` (`/api/events`, `/api/stats`,
  `/api/events/{id}/snapshot`, `POST /api/events/{id}/alert`, `WS /api/ws/events`)

The dashboard shows:

| | |
|---|---|
| **Tiles** | employees · recognized today · unknown today · events today · open alerts · processing fps |
| **Live** | `/live.mjpg` with boxes, names, identity-state colours, running counters |
| **Events** | newest first, snapshot thumbnail, name / status badge, confidence; filter by status / alert / name; click for full evidence; **ack / close** open alerts inline |

New events arrive live over the WebSocket (~1 s).

## Setup

```bash
.venv\Scripts\python.exe -m pip install -r phase5_dashboard/requirements.txt
```

⚠️ **ChromaDB pulls CPU `onnxruntime`, which shadows `onnxruntime-gpu`** and makes
face recognition ~50× slower. After installing, restore the GPU build:
```bash
.venv\Scripts\python.exe -m pip uninstall -y onnxruntime
.venv\Scripts\python.exe -m pip install --force-reinstall --no-deps onnxruntime-gpu==1.20.2
```
Check on startup: the log line `Face engine ready (... {'detection': 'CUDAExecutionProvider', ...})`.

## Use

1. Enrol at least one person (writes to Chroma **and** syncs the dashboard's
   employee list):
   ```bash
   .venv\Scripts\python.exe -m phase3_identity.enroll --code EMP001 --name "..." photos\*.jpg
   ```
2. Start the dashboard:
   ```bash
   .venv\Scripts\python.exe -m phase5_dashboard
   ```
3. Open **http://127.0.0.1:8000**. First start does a ~15 s model warm-up before
   video appears.

`API_HOST` / `API_PORT` in `.env` change where it binds (default `127.0.0.1:8000`).

## ChromaDB

`phase3_identity/gallery.py` now uses `chromadb.PersistentClient` (embedded, on
disk at `GALLERY_DIR`, default `phase3_identity/gallery_chroma/`), cosine space.

Embedded Chroma is **single-process**. The dashboard is one process, so that's
fine. The standalone `phase4_events.api` no longer opens Chroma — it reads the
employee list from a mirror table in the events DB that the pipeline / enrol tool
keep in sync. For genuine multi-process access, run `chroma run --path <dir>` and
set `GALLERY_CHROMA_URL=host:port`.

## Still to come

- Live view is MJPEG (simple, ~15 fps, one stream). WebRTC / multi-viewer later.
- No auth yet — bind to `127.0.0.1` (default) or put it behind something.
- Retention / purge of events + snapshots (acceptance doc §23) not implemented.
- The "cameras online/offline" tiles from BRD §17 wait for multi-camera (Phase 8).
