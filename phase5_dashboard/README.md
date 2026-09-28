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

## Settings page

The **Settings** item at the bottom of the sidebar edits `phase1_camera/.env`,
the one file every phase reads at startup, so there's no second copy of
configuration to drift. It covers the main camera, detection, face recognition,
events, email, queue/footfall tuning, and the separate shelf and vehicle cameras.

- **Changes apply on restart.** Each process reads `.env` once at startup, so a
  saved field shows *Restart pending* and a banner says which process to
  restart. After the restart the badge clears.
- **Test connection** (per camera) opens the stream you typed, before you save,
  and shows resolution, fps and a live frame. Testing the camera the dashboard
  is already streaming reuses its current frame instead of opening a second
  connection, since many cameras accept only one client.
- **Saves edit the file in place.** Comments, ordering and inline notes are kept.
  Keys not yet in the file are appended under a marker comment. The previous
  version is kept as `.env.bak` (git-ignored, like `.env`).
- **Secrets are write-only.** Passwords are never sent back to the browser (a
  blank password field keeps the stored one), and credentials inside camera URLs
  are shown as `user:***@`.
- **Only the machine running the dashboard can change settings.** Anyone who
  can reach the dashboard can view this page, but saving and camera tests are
  refused from other machines, because the page edits credentials and the
  dashboard has no login. Set `SETTINGS_ALLOW_REMOTE=true` in `.env` to lift
  this (only on a trusted network). It deliberately can't be changed from the
  page itself. `API_HOST`/`API_PORT` aren't editable either.

API (on the dashboard app, not the standalone Phase 4 API):

| Method | Path | |
|---|---|---|
| GET  | `/api/settings` | sections, fields, current values, pending/overridden flags |
| PUT  | `/api/settings` | `{"values": {KEY: value}}`: validated all-or-nothing, 400 with per-field errors |
| POST | `/api/settings/test-camera` | `{prefix, url, user, password, transport}` → `{ok, resolution, fps, preview}` |

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
