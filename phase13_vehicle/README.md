# Reality Connector — Phase 13: Vehicle Entry/Exit Tracking (License Plate)

Task 2: track a specific vehicle's entry and exit across cameras, the way
Phase 11 does for employees — except there's no existing "vehicle
recognition" the way Phase 3 gives faces, so this phase had to build one:
generic vehicle detection + generic OCR, filtered and voted into a plate
number that becomes the vehicle's identity.

```
gate-in camera  → detect vehicle, track it, OCR the crop every ~1.5s
                → 3 readings agree (majority vote) → plate confirmed
                → vehicle_event written, same events.db every other phase uses
gate-out camera → same vehicle, new track, new OCR votes → confirmed again
                → vehicle-log: TN01AB1234  in 08:00 (gate-in)  out 18:00 (gate-out)
```

## Design: reuse generic capabilities, like Phase 8's bottle detection

No custom-trained plate detector (no labeled dataset or training pipeline
in this environment) and no dedicated plate-localization model at all:

1. **Vehicle detection** — YOLO's existing COCO classes (2=car, 3=motorcycle,
   5=bus, 7=truck), reusing Phase 2's generic tracker (`PersonTracker`,
   despite the name — it's just YOLO+BoT-SORT with a configurable class
   list) with vehicle classes instead of person.
2. **Plate reading** — crop the tracked vehicle's box, run general-purpose
   OCR (EasyOCR) on the crop, keep only results shaped like a plate
   (`is_plate_like`: 5-11 alphanumeric characters, at least one letter and
   one digit — filters out background text and pure-word/pure-digit noise).
   No dedicated plate-region model needed; the vehicle crop is already a
   tight enough search area.
3. **Identity** — the plate string itself is the identity (like
   `employee_code`), not an embedding — no gallery/database of known
   vehicles needed, just exact-string tracking of whatever gets read.
4. **Entry/exit** — reuses Phase 11's `camera_roles` table unchanged. A
   camera's entry/exit role doesn't know or care whether it's watching
   people or cars.

## Why one OCR reading is never trusted alone

Testing this in isolation found EasyOCR reliably misreads visually similar
characters (**0 read as O**) even on a clean, high-confidence synthetic
plate image — a real, inherent OCR limitation, not a bug to "fix" in the
library. Phase 3 already established the right answer for exactly this
kind of per-frame noise (multi-frame confirmation before committing an
identity); this applies the same idea to plates:

- A plate is only **committed** once `VEHICLE_MIN_READINGS` (default 3)
  OCR attempts on the *same tracked vehicle* agree by majority vote.
- A brief sighting that never reaches that many readings still gets logged
  (best-effort) the moment its track disappears, rather than being
  silently dropped.
- **Important nuance found testing the full pipeline**, not just the
  isolated OCR call: majority voting only helps against readings that
  *vary* frame to frame (real motion, angle, lighting change on a moving
  vehicle). Feeding OCR the *same* static crop repeatedly gets the *same*
  answer every time — if that one answer is a misread, voting cannot
  rescue it, because there's nothing to out-vote it with. This is why
  **every committed plate always keeps an evidence snapshot** — a human
  glancing at it catches a 0/O-type misread instantly; the system doesn't
  pretend OCR is perfect.

## Setup

New dependency — see `requirements.txt` for the install + the
`opencv-python-headless` conflict it introduces and how to fix it (same
"a new package shadows an existing pinned one" pattern as the recurring
onnxruntime-gpu issue).

```
pip install -r phase13_vehicle/requirements.txt
```

Then, in `phase1_camera/.env` (same `VEHICLE_`-prefixed pattern as Phase 8's
`SHELF_` camera):

```
VEHICLE_CAMERA_ID=gate-in
VEHICLE_CAMERA_SOURCE=0                 # or VEHICLE_RTSP_URL=...
VEHICLE_YOLO_CLASSES=2,3,5,7            # car, motorcycle, bus, truck
VEHICLE_MIN_READINGS=3                  # OCR reads needed to commit a plate
VEHICLE_OCR_INTERVAL_S=1.5              # min seconds between OCR attempts, same track
```

Run it (a second, independent process, same as Phase 8):
```bash
python -m phase13_vehicle.app
```

Tag `gate-in`/`gate-out` as entry/exit from the dashboard's **🕒 Attendance**
panel (camera roles are shared across Phases 11/13), then check the new
**🚗 Vehicles** panel for the vehicle log and recent plate reads (with
snapshots).

## Data model

| Table | Holds |
|---|---|
| `vehicle_events` | one row per **committed** plate sighting (majority vote already applied) - never one row per frame |
| `camera_roles` | shared with Phase 11 |

## API (mounted at `/api` by the dashboard)

| Method | Path | |
|---|---|---|
| GET | `/vehicle-events?camera_id=&limit=` | raw committed-sighting log |
| GET | `/vehicle-events/{id}/snapshot` | image/jpeg evidence |
| GET | `/vehicle-log?date=YYYY-MM-DD` | per-plate entry/exit across every camera, mirrors `/attendance` |
| GET | `/track-vehicle/{plate}?date=&limit=` | current camera + recent path, mirrors `/track/{employee_code}` |

## Verified

Pure logic (no GPU needed): `is_plate_like`/`normalize_plate_text`
filtering; `PlateAccumulator`'s majority vote correctly beating a single
misread; throttling; confirmed-track gating (never re-OCR a plate already
committed); force-commit for a brief, below-threshold sighting; stale-track
pruning. Store/API: cross-camera entry+exit aggregation (identical
semantics to Phase 11, reusing `camera_roles`); current-camera tracking;
an internal-only-camera sighting contributing no entry/exit.

**Full pipeline, end to end** (no real vehicle+plate photo available in
this environment, disclosed): a synthetic plate image composited onto the
real bus detected in the project's existing `bus.jpg` test asset (COCO
class 5), run through the actual `VehicleTracker` for several frames —
correctly detected the vehicle, tracked it, cropped it, read the plate via
OCR, accumulated votes, and committed one `vehicle_event` with a saved
annotated snapshot. The committed read carried the known 0/O ambiguity
(consistent across all 3/3 votes, since the test repeated identical static
frames rather than genuinely varying ones) — exactly the "always check the
snapshot" limitation documented above, caught by this test rather than
glossed over. Dashboard UI verified live: vehicle log rendering with
in/out/status per plate, recent-reads list with confidence/vote-count, the
"Vehicles · today" tile.

## Not yet built

- Live camera test with a real, physically moving vehicle (no vehicle
  camera available in this environment) - the full pipeline is proven
  end-to-end on a composite image; real footage would additionally test
  whether genuine frame-to-frame variation lets majority voting correct an
  ambiguous character the way it's designed to (the composite test
  couldn't, by construction — see above).
- Plate-format-aware character correction (e.g. "position N is always a
  digit in this region's format, so a read 'O' there gets corrected to
  '0'") - would meaningfully improve accuracy beyond generic OCR + voting,
  but needs a specific target plate format to be worth implementing.
