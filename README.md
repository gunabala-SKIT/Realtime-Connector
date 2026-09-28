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
| **Phase 14** | PPE / safety compliance — per-zone required items (helmet, vest, …), custom-trained detector, confirmed violations with evidence snapshots | Done — [`phase14_ppe/`](phase14_ppe/README.md). Gloves detection unreliable (see its README). |
| Phase 15+ | Multi-camera production hardening | Not started |

**Separate product in this repo:** [`manufacturing_intel/`](manufacturing_intel/README.md) - *Manufacturing Process
Intelligence Platform* (stage-by-stage production tracking, waiting vs processing time, bottlenecks, rejects,
yield, alerts, assistant). Own database, port (8100) and dashboard; imports nothing from the phases above.

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

## Moving to another machine

The code lives entirely in git; several things don't, since they're either
secrets, personal data, or auto-generated (`.gitignore`) — copy these
separately if you want the new machine fully equivalent, not just running
the same code:

| What | Where | Why it's not in git |
|---|---|---|
| Camera settings + credentials | `phase1_camera/.env` | secrets |
| Enrolled employee faces | `phase3_identity/gallery_chroma/` | personal biometric data — can't be regenerated, only re-enrolled |
| Enrollment source photos (only if you want to re-enroll instead) | `img/`, `enroll_photos/` | personal photos |
| Event history (optional) | `phase4_events/events.db` | local runtime data, regenerates empty on first run |
| Model weights (optional — auto-download on first run) | `*.pt` files | large binaries, auto-fetched. Exception: `phase14_ppe/models/ppe.pt` is custom-trained, so it *is* in git. |

### Getting the code across

No git remote is configured on this repo yet (`git remote -v` is empty) — pick one:

**A. Push to a private GitHub/GitLab repo** (best for ongoing work on both machines):
```bash
gh repo create reality-connector --private --source=. --push
```
Then on the other machine: `git clone <url>`.

**B. One-time offline transfer** (USB drive, no account needed) — carries full commit history, same as a normal clone:
```bash
git bundle create reality-connector.bundle --all
```
Copy the file over, then on the other machine: `git clone reality-connector.bundle reality-connector`.

Either way, copy the files from the table above separately — a clone/bundle only includes what git tracks.

### Setting up the new machine

1. Python 3.12, Windows (this project is Windows-native — PowerShell, backslash paths — not tested on Linux/Docker).
2. Run `nvidia-smi` on the new machine **before** installing anything. This project is pinned to CUDA 12.4 / driver-560-class hardware (`onnxruntime-gpu==1.20.2` needs driver <580); a different GPU/driver may need different pins throughout the file below.
3. Follow the install sequence at the top of [`requirements-freeze.txt`](requirements-freeze.txt) exactly — order matters (torch needs its own CUDA index URL; two packages need a fix-up *after* the bulk install, the same "a later package silently shadows a pinned GPU one" pattern documented in several phase READMEs, e.g. [`phase5_dashboard/requirements.txt`](phase5_dashboard/requirements.txt), [`phase13_vehicle/requirements.txt`](phase13_vehicle/requirements.txt)).
4. Copy `.env`, `gallery_chroma/`, and anything else you want from the table above into place.
5. Verify: `python -m phase1_camera.check_network`, then `python -m phase5_dashboard` and confirm the dashboard loads at `http://127.0.0.1:8000` with your enrolled employees showing (`gallery=N` in the startup log, N > 0 if you copied the gallery over).

If you'll keep using Claude Code on the new machine too: this session's accumulated memory (lessons learned, gotchas) lives outside the repo, in `~/.claude/projects/.../memory/` on *this* machine — copy that folder to the equivalent path on the new one if you want a fresh session to start with the same context. Not strictly required: every phase's README already documents its own gotchas and lessons in similar detail, so a fresh session reading the repo can reconstruct most of it on its own.
