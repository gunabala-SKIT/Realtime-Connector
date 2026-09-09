# Reality Connector — Phase 3: Face Recognition + Identity States

Adds the identity layer on top of Phase 2 tracks:

```
person track (Phase 2)
  → SCRFD face detection (InsightFace)
  → face inside the person box?  → quality gate (IOD · yaw · blur · det score)
  → ArcFace 512-d embedding
  → cosine k-NN vs enrolled employees (local SQLite gallery)
  → per-track state:  RECOGNIZED · UNKNOWN · UNVERIFIED
```

`phase1_camera` and `phase2_detect` are untouched — this imports `CameraStream`,
`LatestFrameStream`, and `PersonTracker` from them.

## Setup

Phase 1 + 2 venv, plus InsightFace on the **GPU** onnxruntime:

```bash
.venv\Scripts\python.exe -m pip install -r phase3_identity/requirements.txt
```

**Watch out:** `insightface` pulls in CPU `onnxruntime`, which shadows the GPU
build. If `onnxruntime.get_available_providers()` doesn't list
`CUDAExecutionProvider`:
```bash
.venv\Scripts\python.exe -m pip uninstall -y onnxruntime onnxruntime-gpu
.venv\Scripts\python.exe -m pip install onnxruntime-gpu
```

The model pack (`buffalo_l`, ~280 MB — SCRFD detector + ArcFace `w600k_r50`
recogniser) auto-downloads on first run to `~/.insightface/models`. Only the
detection + recognition sub-models are loaded (landmarks / gender-age skipped).

`faces.py` adds the CUDA/cuDNN DLL directories from the `nvidia-*-cu12` wheels and
torch's bundle to the search path and calls `onnxruntime.preload_dlls()` —
without this ORT silently runs on CPU (~2.4 s/frame instead of ~50 ms). On start
it logs which execution provider each model got.

**GTX 1660 timings:** face detect + embed ≈ 40–70 ms (1–6 faces) on top of
person detection ≈ 40 ms → ~8–12 fps processed. The threaded capture keeps it
live by dropping frames.

## 1. Enrol employees

```bash
.venv\Scripts\python.exe -m phase3_identity.enroll ^
    --code EMP001 --name "Asha Rao" --dept Engineering ^
    C:\path\to\asha\*.jpg
```

- 3+ frontal photos per person is ideal; 1 works.
- Each image must have **exactly one** face that passes the quality gate — bad
  ones are rejected with a reason (no face / multiple faces / small / blurred / turned).
- Embeddings are averaged and L2-normalised, stored in `phase3_identity/gallery.db`.

```bash
.venv\Scripts\python.exe -m phase3_identity.enroll --code EMP001 --name X --disable   # (not yet — use SQL)
```

## 2. Run

```bash
.venv\Scripts\python.exe -m phase3_identity.app
```
```bash
.venv\Scripts\python.exe -m phase3_identity.app --no-display --duration 600
.venv\Scripts\python.exe -m phase3_identity.app --save phase3_identity/logs/demo.mp4
```

Box colours: **green** RECOGNIZED · **red** UNKNOWN · **amber** UNVERIFIED.
Label shows `ID <n>  <name> (<score>)`. Overlay shows per-state counts, gallery
size, and person/face inference times.

## Identity logic (BRD §14, acceptance doc §5.4–5.6)

| State | When |
|---|---|
| `UNVERIFIED` | the track has never had a face pass the quality gate — **no identity claim is made** |
| `RECOGNIZED` | ≥ `IDENTITY_CONFIRM_FRAMES` quality-passed faces matched an employee at cosine ≥ `RECOGNITION_THRESHOLD` |
| `UNKNOWN` | ≥ `IDENTITY_CONFIRM_FRAMES` quality-passed faces with best match < threshold |

RECOGNIZED↔UNKNOWN flips are rate-limited by `IDENTITY_FLIP_COOLDOWN_S`.
Recognition runs every frame while a track is UNVERIFIED, then every
`RECOGNIZE_EVERY_N` frames to save GPU.

## Config (`phase1_camera/.env`)

| Key | Default | Meaning |
|---|---|---|
| `FACE_MODEL_PACK` | `buffalo_l` | `buffalo_s` is lighter/faster, less accurate |
| `FACE_DET_SIZE` | `640` | SCRFD input size |
| `FACE_MIN_IOD` | `40` | quality gate — inter-ocular distance in px (≈ how close/large the face is) |
| `FACE_MAX_YAW` | `30` | quality gate — head turn in degrees |
| `FACE_BLUR_MIN` | `55` | quality gate — variance of Laplacian |
| `RECOGNITION_THRESHOLD` | `0.35` | cosine operating point. Raise → fewer false matches, more UNKNOWN. Lower → opposite. **Calibrate and record this** (acceptance §5.4). |
| `RECOGNIZE_EVERY_N` | `5` | per-track cadence once identity is settled |
| `IDENTITY_CONFIRM_FRAMES` | `3` | multi-frame confirmation |

## Tuning the threshold

ArcFace `w600k_r50` cosine similarity, rough guide: same person **0.4–0.7**,
different people **0.0–0.25**. `0.35` is a conservative start. To calibrate:
enrol a few people, run the app, watch the `score` on their labels and on
strangers, then set the threshold in the gap.

## Notes

- Face detection runs on the **whole frame** once per processed frame, then faces
  are matched to person tracks by "face centre inside person box". A person with
  0 or ≥2 faces in their box stays UNVERIFIED that frame.
- No liveness / anti-spoofing (a photo can defeat it) — that's out of MVP scope
  (acceptance doc §9).
- **Licensing:** InsightFace pretrained models are for non-commercial research
  use. A commercial deployment needs a licensed/retrained model (acceptance doc,
  Assumptions & Dependencies).
- The local SQLite gallery is the stand-in for PostgreSQL + pgvector, which
  arrives with the backend in Phase 4/5.
