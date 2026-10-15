# Garment Manufacturing Computer Vision System

Built from `garment_product_prompt.md` (kept by the user). A separate product from Reality
Connector and from `manufacturing_intel/`: own package, own port (8200), imports nothing from
either. Full roadmap and the reasoning behind what's *not* built yet: [`PLAN.md`](PLAN.md).

**All seven MVP phases (spec section 21) are now built: camera, object detection, tracking, zones,
activity detection, production events + database, and a dashboard.** What's deliberately still
missing — a real garment-trained detection model and a dedicated frontend build — is covered in
"What comes next" below. No AI assistant / chatbot of any kind is planned for this product at all.

## What's built

### Phase 1 — camera module

Connects to one camera (RTSP URL, webcam, or a video file for testing), reads frames reliably,
reconnects automatically if the connection drops, and serves the live picture plus connection
status over HTTP — with nothing hard-coded (spec section 25): source, credentials, timeouts and
retry policy all come from environment variables.

### Phase 2 — object detection (COCO stand-in)

The user's explicit choice, since there's no real garment-factory training data yet: run a stock
COCO-pretrained YOLO model as a placeholder, so the detect → display pipeline can be built and
tested now, and swapped for a real garment-trained model later by changing one setting.

**Only `person`, `scissors` and COCO's `dining table` (→ `table`) genuinely match** what the spec
asks for (spec section 6). The rest of the required list — `fabric`, `marking_tool`,
`measuring_scale`, `pattern`, `sewing_machine`, `fabric_piece`, `bundle` — has nothing resembling
it anywhere in the standard 80 COCO classes, so **this detector does not produce them at all**,
rather than mapping them to some keyword-similar COCO class that would just be a confident-looking
wrong answer. This is disclosed everywhere it matters: the status page, `/api/detections`, and
`backend/detection/coco_standin.py`'s own docstring. Same principle as Reality Connector's Phase 8
shelf-monitoring stand-in, and PPE's disclosed dataset limitations.

### Phase 3 — object tracking

Each detected object gets a temporary ID that stays the same across frames while it's in view
(spec section 9's example: `Person ID: 12`, `Scissors ID: 7`). Default tracker is **BoT-SORT**
(adds camera-motion compensation and appearance re-identification on top of ByteTrack; also the
default already used by Reality Connector's own `phase2_detect` module) — configurable to plain
ByteTrack. Detection and tracking share one class: Ultralytics implements tracking as a mode of
the *same* model call (`model.track()` instead of `model.predict()`), carrying state on that one
model object, not as an independent stage bolted on after — see `backend/tracking/config.py`'s
docstring for the full reasoning, and why `backend/tracking/` holds only the tracker's settings
rather than its own detector class.

### Phase 4 — zones

An administrator can draw a polygon on the camera view (Marking Zone, Cutting Zone, Stitching
Zone, ...) and the system reports which zone (if any) each tracked person/object is standing in
(spec section 10). Unlike tracking, zone membership needs nothing from the YOLO model — checking
whether a box's ground point falls inside a polygon is pure geometry — so `backend/zones/` *is*
its own independent module exactly as the spec's own folder structure describes, with zero
knowledge of cameras, detection or tracking. `ObjectDetector` reaches it only through one small
method (`zone_for_box`), so neither side needs to import the other's internals.

No database existed yet when this phase was built (that came later, in Phase 6), so zones are
persisted to a small JSON file (`configs/zones.json` by default), one write-through in-memory list
per process — left that way even after Phase 6 added a real database, since it already works and
migrating it wasn't needed by anything Phase 6/7 actually required. A plain utility
page at `/zones` lets you click points on the live picture to draw and save a zone, and delete
existing ones — not the dashboard (Phase 7), just enough to configure and see zones working. Each
zone can also be tagged with what kind of work happens there (`cutting`, `marking`, `stitching`,
`piece_handling`) via a dropdown on that same page — that tag is what Phase 5's rule engine keys on.

### Phase 5 — activity detection

A rule engine turns object + zone + movement + sustained duration into a named activity for every
tracked person (spec sections 8, 11, 22) — never a single frame's object presence alone (spec
section 3, word for word: *"Worker holding scissors ≠ Cutting"*). `backend/activities/` doesn't
touch the camera, model, or zone-storage code at all: `ActivityEngine.update()` takes plain
per-frame data (already-tracked, already-zoned detections) and returns what each tracked person is
currently doing, so it's fully testable with hand-built scenarios and no GPU needed
(`tests/test_activity_engine.py`).

- **Rules are data, not if-statements** (`activities/rules.py`), per the spec's own words: "the
  rule engine must be configurable rather than hard-coded wherever possible" (section 11). Each
  `ActivityRule` says: which zone-activity it needs, which detected class must be attributed to the
  person, and whether that object must show real movement (not just be present) — CUTTING requires
  movement precisely because a static scissors detection must never count as cutting.
- **Given the COCO stand-in, only CUTTING can actually fire** (person + scissors + a "cutting" zone
  + real scissors movement). With a garment-trained model the engine is told what the model can see
  (`ActivityEngine.configure`), `reachable_now`/`dormant` follow from that, and CUTTING additionally
  requires the scissors to be touching cloth - the spec's own "Fabric detected" condition, restored
  once a model can see cloth.
- **"Movement" is measured on real footage, not assumed.** The first version needed the scissors'
  centre to drift 3% of the frame diagonal; real cutting (hands stay put, blades snip) never did
  that, so it never fired. Replaying real clips showed scissors lying still spread 1-6 px over a 2 s
  window and real cutting 11-57 px, so movement is now the spread of the box's position *and size*,
  threshold 0.28% of the diagonal. Both new conditions were checked against real clips: with only
  the lower threshold, "holding scissors, not cutting" also fired CUTTING; with only the cloth
  contact rule, real cutting still didn't. Calibrated on a single short session by one person, so
  treat the margin (6 px vs 11 px) as thin. MARKING/STITCHING/PIECE_HANDLING are fully implemented and fully
  tested — thresholds, zone-activity matching, the works — but structurally dormant: their required
  classes (`marking_tool`, `sewing_machine`, `fabric_piece`) don't exist anywhere in this model's
  80-class vocabulary. `GET /api/activities` and the status page both say so explicitly
  (`reachable_now` / `dormant`), the same honesty pattern Phase 2 already uses for undetectable
  classes. CUTTING itself also quietly drops the spec's own "Fabric detected" condition, since
  fabric has no COCO stand-in either — disclosed in `rules.py`'s own docstring.
- **An object is attributed to a person** by nearest-center-within-an-expanded-box matching (a hand
  holding scissors reaches past the body outline), not just "closest object anywhere in frame."
- **Confirm-then-cooldown**, the same shape as PPE compliance and footfall counting elsewhere in
  this repo: an activity confirms once its condition has held continuously for a configurable
  minimum duration, and survives a gap shorter than `grace_s` (one missed detection frame — an
  occlusion, a hand briefly blocking the view — must not restart the clock from zero).
- **IDLE** is just another rule: it confirms whenever nothing else's raw condition holds, so every
  tracked person always has a definite state, and gets the same grace-period tolerance as every
  other activity.
- Completed activities (with a start/end time, duration and average confidence) are kept in memory
  (capped at a configurable count) *and* persisted to the database — see Phase 6 below.

### Phase 6 — production events + database

Every completed activity (Phase 5's `CompletedActivity`) becomes a persisted `ProductionEvent`
(spec section 12: Start, End, Duration, Worker, Camera, Zone, Confidence), stored in a real
database for the first time in this product.

- **SQLite, not the spec's own "recommended" PostgreSQL** (section 23) — a single-camera MVP
  (section 20) has no concurrent-writer problem a file database can't handle, and swapping to a
  server-based database later is a connection-string change, not a rewrite, since every query goes
  through one module (`backend/database/db.py`).
- **A single shared connection guarded by one `threading.Lock`** — the exact fix already proven
  elsewhere in this repo for the same bug class (a shared `sqlite3` connection used from multiple
  threads without synchronization causes intermittent corruption/`IndexError`/`InterfaceError`
  under real concurrent load, not under a single-threaded test). Verified here with a genuine
  10-thread, 200-write concurrency test, not just single-threaded CRUD.
- **Only two tables exist**: `production_events` (this phase's actual deliverable) and `workers`
  (spec section 13's explicit MVP allowance — manual `track_id` → name assignment, no face
  recognition required). The much longer entity list in spec section 19 (`Camera`,
  `ProductionOrder`, `FabricBatch`, `Bundle`, `Machine`, ...) is that section's own *suggested* full
  schema for the finished product, not a Phase 6 requirement — building tables nothing yet reads or
  writes would be speculative, disclosed in `database/db.py`'s own docstring.
- **IDLE completions are persisted too**, not just the four production stages, since spec section
  17's "Idle Time" calculation needs a real record of idle spans, not an inferred gap.
- **`ActivityEngine` never imports `events/` or `database/` itself** — it calls a plain
  `on_complete` callback it was given at construction, the same decoupling pattern as every earlier
  phase's small protocol classes (`_ZoneLookup`, `_ActivityLookup`). `main.py` is the only place
  that wires the callback to an actual database write.
- A tracked person's worker name is resolved at the moment their event is recorded: an assigned
  name (`POST /api/workers`) if one exists for that `track_id`, else the `track-{id}` fallback.
  Since track IDs are temporary (Phase 3), an assignment means "while this ID is active, call it
  this name" — not a permanent roster. A real one needs a real identification method, which spec
  section 13 explicitly defers past the MVP.

### Phase 7 — dashboard

One server-rendered page at `/dashboard` (`backend/api/dashboard.py`) — plain HTML and vanilla JS,
the same style every earlier phase's page already uses, not a separate frontend build. Four tabs:

- **Live Monitoring** (spec section 15): the raw `/live.mjpg` stream with a `<canvas>` overlay
  drawn **client-side**, polling `/api/detections`, `/api/activities` and `/api/zones` — not the
  already-existing `/live/annotated.mjpg`. This is a deliberate choice, not an oversight:
  `ObjectDetector` renders one shared annotated JPEG per detection cycle for every viewer (see its
  module docstring), so there's no way to honor a per-viewer "hide zones" toggle (section 15's own
  requirement) on a stream every viewer shares — Phase 4's `/zones` page already proved this exact
  canvas-over-raw-image technique works. Five independent toggles (bounding boxes, object labels,
  tracking IDs, zones, activity labels) plus a side panel per tracked person (name, current
  activity, zone, live duration, and an inline "assign name" field).
- **Production Timeline** (spec section 16): one row per worker, positioned stage blocks across a
  real time axis, built from `GET /api/events`.
- **Activity History** (spec section 17): a filterable table of persisted events.
- **Analytics** (spec sections 17–18): stat cards (active workers per stage, completed activities),
  a stage-performance table (count/average/total time per stage), and bottleneck detection —
  configured baseline vs. measured average, flagged "Needs Investigation" only when exceeded, never
  a guessed cause (section 18's explicit instruction).

```
garment_cv/backend/
  config.py            process settings (host, port, logging)
  logger.py            logging with credential redaction
  main.py              entrypoint: wires config -> camera -> zones -> detector(+tracker) -> API
  cameras/
    config.py           CameraConfig - all settings, from env vars
    stream.py            CameraStream - connect / read / validate / reconnect / metrics
    runner.py            CameraRunner - runs a CameraStream on a background thread, keeps the
                          latest frame ready to serve, and exposes `frame_hooks` (see
                          "How detection plugs in" below) - unchanged since Phase 1
  detection/
    coco_standin.py       the COCO -> garment class map, and what's NOT covered
    config.py             DetectionConfig - all settings, from env vars
    detector.py            ObjectDetector - runs YOLO (detection and/or tracking), attaches
                            zone membership, draws boxes/IDs/zones, throttled per frame_hooks
  tracking/
    config.py             TrackingConfig - tracker choice and on/off, from env vars (see its
                          docstring for why there's no separate tracker class)
  zones/
    geometry.py            point-in-polygon test + the "ground point" (bottom-centre) rule
    store.py               Zone dataclass + ZoneStore - JSON-file-backed CRUD (left that way even
                            after Phase 6 added a database); a zone also carries an optional
                            `activity` tag (Phase 5)
    resolver.py            ZoneResolver - "which zone/activity is this box in", the only thing
                            the detector talks to
    config.py               where the zones file lives, from an env var
  activities/
    rules.py               ActivityRule table - data, not if-statements; discloses which rules
                            can actually fire given the COCO stand-in
    config.py              ActivityConfig - every duration/movement/margin threshold, from env vars
    engine.py               ActivityEngine - the confirm-then-cooldown rule engine itself, calls
                            an `on_complete` callback (Phase 6) with each finished activity
  database/
    db.py                   Database - SQLite schema + a single lock-guarded connection
    config.py               where the database file lives, from an env var
  events/
    store.py                ProductionEvent dataclass + EventStore - record/list/aggregate queries
    baselines.py             bottleneck baselines, from env vars - no baseline, no comparison
  workers/
    store.py                WorkerStore - manual track_id -> name assignment (spec section 13)
  api/
    routes.py            FastAPI: status page, zone-drawing page, JSON status, live views (raw
                          + annotated), detections, zone CRUD, activities, events, analytics,
                          worker assignment, manual reconnect
    dashboard.py          the Phase 7 dashboard page's HTML/JS template
```

## Running it

```
cp garment_cv/configs/.env.example garment_cv/configs/.env
# edit garment_cv/configs/.env: set GARMENT_CAMERA_SOURCE to your camera (or a video file to try
# it without one - see "Trying it without a camera" below)
python -m garment_cv
```

Open `http://127.0.0.1:8200` (configurable via `GARMENT_HOST`/`GARMENT_PORT`). That page is a bare
status view — a live image (with detection boxes, track IDs and zone outlines, if detection is
on), a connection-status table, which classes are/aren't detected, and links to `/zones` and the
real dashboard at `/dashboard`.

| Route | |
|---|---|
| `GET /` | status page with the live image |
| `GET /zones` | zone-drawing utility page (click points on the live picture, name, save, delete) |
| `GET /api/status` | camera JSON: connected, resolution, fps, frame counts, reconnections, last error |
| `GET /live.mjpg` | the raw live view (MJPEG), never has detection boxes or zones drawn on it |
| `GET /live/frame.jpg` | one current raw JPEG frame |
| `POST /api/reconnect` | force a camera reconnect attempt (mainly for testing) |
| `GET /api/detections` | latest detections (`track_id`, `zone`) + which spec classes aren't detectable yet |
| `GET /live/annotated.mjpg` | the live view with detection boxes, track IDs and zone outlines drawn |
| `GET /api/zones?camera_id=` | list configured zones |
| `POST /api/zones` | add a zone: `{camera_id, name, points: [[x, y], ...]}` (0-1 fractions of the frame) |
| `PATCH /api/zones/{id}` | rename a zone and/or replace its points |
| `DELETE /api/zones/{id}` | remove a zone |
| `GET /api/activities` | activity engine status (rules, reachable/dormant), each tracked person's current activity, and recently completed activities |
| `GET /api/events` | persisted production events, filterable by `camera_id`/`stage`/`worker`/`since_ts` |
| `GET /api/analytics` | per-stage count/average/total time, worker active/idle seconds, and any configured bottleneck comparisons |
| `GET /api/workers` | list manual worker-name assignments |
| `POST /api/workers` | assign a name to a track: `{track_id, name}` |
| `DELETE /api/workers/{track_id}` | remove an assignment (reverts to the `track-{id}` fallback) |
| `GET /dashboard` | the production monitoring dashboard: live view, timeline, history, analytics |

Detection can be turned off entirely with `GARMENT_DETECT_ENABLED=false` in `.env` — the app then
behaves exactly like Phase 1 (the detection and zone routes disappear; `/` says so). Tracking can
be turned off on its own with `GARMENT_TRACK_ENABLED=false` (`track_id` is then always `null`).
Zones work whether or not tracking is on — `zone` is set on every detected object, tracked or not.

### Trying it without a camera

```
python -m garment_cv.tests.make_test_clip garment_cv/media/sample.mp4
# in garment_cv/configs/.env: GARMENT_CAMERA_SOURCE=garment_cv/media/sample.mp4
python -m garment_cv
```

A video file source is paced to its own frame rate and loops at the end, so it behaves like a
live camera for testing (the same technique `manufacturing_intel` uses for its camera demo). Note
`make_test_clip`'s synthetic video (a moving coloured square) won't trigger any *real* person
detections, trackable IDs, or meaningful zone membership — for that, use a real photo or clip of
people, the way the automated tests do (see below).

### Settings

All in `garment_cv/configs/.env` (see `.env.example` for the full list with defaults).

- **Camera:** `GARMENT_CAMERA_SOURCE`, `GARMENT_CAMERA_USER`/`_PASSWORD` (merged into the URL,
  never logged), `GARMENT_CAMERA_ID`/`_NAME`, `GARMENT_RTSP_TRANSPORT`, timeouts and
  retry/reconnect policy, `GARMENT_STREAM_MAX_WIDTH`/`_JPEG_QUALITY`.
- **Detection:** `GARMENT_DETECT_ENABLED`, `_MODEL` (any Ultralytics-compatible weights file —
  this is the one setting to change for a real garment model later), `_DEVICE`, `_CONF`, `_IOU`,
  `_IMGSZ`, `_HALF` (**keep `false` on GTX 16-series GPUs — no tensor cores, half precision
  produces NaNs there**), `_INTERVAL_S` (minimum time between inference runs, so detection can
  never bottleneck the camera thread), `_STREAM_MAX_WIDTH`/`_JPEG_QUALITY`.
- **Tracking:** `GARMENT_TRACK_ENABLED`, `GARMENT_TRACKER` (`botsort.yaml` or `bytetrack.yaml`).
- **Zones:** `GARMENT_ZONES_FILE` (default `configs/zones.json`, git-ignored — it's per-install
  configuration, not source code).
- **Activity detection:** `GARMENT_ACTIVITY_ENABLED`, per-activity minimum durations
  (`_CUTTING_MIN_S`, `_MARKING_MIN_S`, `_STITCHING_MIN_S`, `_PIECE_HANDLING_MIN_S`, `_IDLE_MIN_S`),
  `_GRACE_S` (how long a condition may drop out before its timer resets), `_MOVEMENT_WINDOW_S`/
  `_MOVEMENT_MIN_FRACTION` (how "is this object moving" is judged), `_ATTRIBUTION_MARGIN` (how far
  a tool may sit outside a person's own box and still count as theirs), `_MAX_HISTORY` (completed
  activities kept in memory).
- **Database:** `GARMENT_DB_FILE` (default `configs/garment.db`, git-ignored).
- **Bottleneck baselines:** `GARMENT_BASELINE_MARKING_S`/`_CUTTING_S`/`_STITCHING_S`/
  `_PIECE_HANDLING_S` — an expected duration per stage, in seconds. Unset by default; a stage with
  no baseline is left out of the dashboard's bottleneck table entirely, never compared against a
  made-up default.
- **Server:** `GARMENT_HOST`/`_PORT`, `GARMENT_LOG_LEVEL`/`_LOG_DIR`.

## How detection, tracking and zones plug in

`CameraRunner.frame_hooks` is a plain list of callables, invoked as `hook(camera_id, frame, ts)`
on every successfully read frame — a seam Phase 1 built for exactly this, and every later phase
used without changing anything in `cameras/`:

```python
db = Database(db_file_path())
event_store, worker_store = EventStore(db), WorkerStore(db)
activity_engine = ActivityEngine(ActivityConfig.load(),
                                 on_complete=lambda c: _record_completed_activity(event_store, worker_store, c))
detector = ObjectDetector(DetectionConfig.load(), log, tracking=TrackingConfig.load(),
                          zones=ZoneResolver(ZoneStore(zones_file_path())),
                          activities=activity_engine)
detector.start()
runner.frame_hooks.append(detector.observe)
```

A hook that raises is caught and logged — one failing hook can't take the camera down (verified
by `test_a_hook_that_raises_does_not_stop_the_camera`). Zone membership itself is pure
post-processing of already-computed detections (`ObjectDetector._with_zones`), so it's tested on
its own with hand-built detections, with no model or GPU needed at all. The activity engine attaches
the same way: `ObjectDetector.observe()` calls `activity_engine.update()` with that frame's already
zoned, already tracked detections right after computing them, and labels the annotated view with
each person's current activity — the engine itself never touches a frame or the camera. The
database attaches one level further out, through the activity engine's own `on_complete` callback —
`activities/` still never imports `events/` or `database/` directly.

## How this was tested

**180 automated tests** (`python -m unittest discover -s garment_cv/tests -t .`, ~20-50s): camera
config parsing and credential handling, real reads against a synthetic test video
(`tests/make_test_clip.py`) including file pacing and end-of-file looping, connect/reconnect
against both a real file and a deliberately invalid source, the background runner and its frame
hooks, the camera API routes, detection/tracking/zones config parsing, zone geometry and storage
(point-in-polygon, the ground-point rule, CRUD, validation, persistence across a restart, a
corrupt file starting empty rather than crashing) with no model needed at all, **the real YOLO
model run against a real photo** (`tests/assets/bus_small.jpg`) verifying it actually finds people
with real confidence scores and correctly-bounded boxes and that it never reports a class it has
no COCO match for, **a real pan built from that same photo** (`tests/test_tracking.py`) verifying
the same real person keeps the same track ID while their box genuinely moves and that a static
repeated scene doesn't relabel a still-visible person as "new" each frame, detection + tracking
+ zones wired end-to-end through the camera → hook → API path, including a real detected person
correctly landing inside a zone drawn over them, a zone's `activity` field (12 tests: add/update/
clear, case-insensitive validation, surviving a restart, an old zones.json with no `activity`
field at all loading with `activity=None`), and **23 hand-built activity-engine scenarios**
(`tests/test_activity_engine.py`) covering: CUTTING confirming only after real sustained scissors
movement inside a "cutting" zone (never from static presence alone — the spec's own "holding ≠
cutting" test, word for word); a short detection gap not resetting the confirmation timer but a
long one does; scissors outside the cutting zone, or too far from the person, never confirming;
MARKING/STITCHING/PIECE_HANDLING correctly firing against synthetic classes that don't exist in
real COCO output yet (proving the dormant rules are genuinely correct code, not silently broken);
IDLE confirming after its own threshold and correctly not confirming while a cutting attempt is
still in progress (even before it's confirmed); multiple tracks kept fully independent; a
completed activity's average confidence over its whole span, including the very first tick before
movement could even be measured; the reachable/dormant status report; and the `on_complete`
callback firing exactly once per finished activity, both on a normal state transition and on a
stale-track prune, with no listener attached raising nothing.

**Phase 6/7 additions**: schema creation and a genuine 10-thread/200-write concurrency test against
the database (not just single-threaded CRUD — the same bug class this repo already hit once for
real, see below); `EventStore`'s record/list/filter/aggregate queries against a real SQLite file;
`WorkerStore`'s assign/reassign/unassign/fallback behaviour; bottleneck-baseline parsing (unset →
`None`, malformed → `None`, never a crash); the full API surface (`/api/events`, `/api/analytics`,
`/api/workers`, `/dashboard`) including that each route disappears entirely (404) when its store
isn't wired in; the `/dashboard` page actually rendering its four tabs; and an end-to-end test
wiring a real `ActivityEngine` straight into a real `EventStore` through the exact `on_complete`
callback `main.py` uses, proving a completed activity really lands in the database with the correct
fallback worker name, and that an assigned name is used instead once one exists.

**Also run against a real, live server** (not just the test suite), over genuine HTTP:
- Camera alone: started `python -m garment_cv` pointed at a test video and confirmed `/`,
  `/api/status`, `/live/frame.jpg` and `POST /api/reconnect` all respond correctly, and read real
  JPEG frames out of the live `/live.mjpg` stream over an actual socket.
- Camera alone, disconnected: pointed it at a path that doesn't exist — it logged a clear error
  every attempt, the process never crashed, it kept retrying on the configured cadence, and
  `/api/status` honestly reported `connected: false` with `last_error` set the whole time.
- Detection: pointed a live server at a clip built from the same real photo, pulled a real frame
  out of `/live/annotated.mjpg`, and visually confirmed correctly placed, correctly labelled boxes
  around real people (4 found, confidence 0.62–0.89) — while `/live/frame.jpg` (the raw stream)
  stayed completely unannotated, proving the two are properly independent.
- Tracking: pointed a live server at a slow back-and-forth pan built from the same photo, polled
  `/api/detections` twice 1.5s apart over real HTTP, and confirmed the same three people kept the
  same track IDs (1, 2, 6) while their box coordinates had genuinely shifted with the pan.
- Zones: drew a real zone over the left half of the frame via `POST /api/zones` on the live
  server, and confirmed `/api/detections` correctly placed the three people standing there inside
  it and the person on the right in none of it — then pulled an annotated frame and visually
  confirmed the zone outline was drawn in the right place with the right label. Separately, opened
  `/zones` in a real browser, clicked four points to draw a second zone, named and saved it through
  the actual page (not the API directly), confirmed it persisted via `/api/zones`, then deleted it
  with the page's own Delete button and confirmed that took effect too.
- Activity detection: started a live server against a 20-second clip built from the real
  `bus_small.jpg` photo (4 real detected/tracked people, no scissors anywhere in it) with
  `GARMENT_ACTIVITY_IDLE_MIN_S` shortened for a fast check, and polled `GET /api/activities` over
  real HTTP — all four settled into `IDLE` after the threshold. Then drew a real "cutting" zone
  (`POST /api/zones`, `activity: "cutting"`) directly over one of those people, confirmed
  `/api/detections` now showed `zone: "Cutting Zone"`, `zone_activity: "cutting"` for them, and
  polled `/api/activities` again — they still settled into `IDLE`, not `CUTTING`, proving that
  standing in a cutting zone with no tool at all still correctly never counts as cutting.
  **CUTTING itself is verified at the unit/integration level only** (`test_activity_engine.py`),
  since no real photo or clip of a person actually using scissors is available — disclosed here
  rather than silently skipped, the same honesty standard applied to every other gap in this phase.
- Production events + database: built a clip from the real photo followed by 40 real seconds of
  empty frames (long enough to cross the stale-track prune threshold), started a live server
  against it, and watched four real tracked people go `IDLE`, leave frame, and get pruned —
  confirmed each one landed as a real row in `/api/events` with the correct `camera_id`, `stage`,
  `duration_s` and `confidence`, and that `/api/analytics` correctly aggregated them (`stage_summary`,
  `worker_idle_seconds`). Assigned a worker name via `POST /api/workers` on the live server and
  confirmed subsequent events for that track used the assigned name instead of the `track-{id}`
  fallback — both over curl and through the dashboard's own UI (see below).
- Dashboard: opened `/dashboard` in a real browser against the same live server and worked through
  all four tabs with real, accumulating data — **Live Monitoring** showed the canvas overlay's
  bounding boxes landing pixel-correctly on the real people in frame (confirming the client-side
  scale-factor fix below), the side panel listing real tracked people with live durations, and
  typed a name into the "assign" field, clicked Save, and watched it immediately appear both in the
  live panel and in a subsequently-persisted history row; **Production Timeline** rendered real
  per-worker IDLE blocks positioned on a real time axis; **Activity History** listed real completed
  events, filterable by stage; **Analytics** showed real stat cards, a real stage-performance table,
  and the bottleneck table correctly reporting "no baselines configured" rather than inventing one.
  Also toggled all five overlay checkboxes off/on and confirmed the draw loop respects each one.

**Two real bugs were found building Phase 7's dashboard, both only visible with a real browser
against a real live server — neither would show up in a server-side unit test:**
1. **Wrong bounding-box scale factor**: the live overlay's canvas initially scaled detection boxes
   using the displayed `<img>` element's `naturalWidth`/`naturalHeight` — but those reflect
   `/live.mjpg`'s own (possibly downscaled, via `GARMENT_STREAM_MAX_WIDTH`) encoded size, while
   YOLO/Ultralytics always rescales its boxes back to the *original* captured frame's resolution.
   On a camera where those two sizes differ, every box would land in the wrong place. Fixed by
   reading the camera's real sensor resolution from `/api/status` instead and scaling against that.
2. **Typed text vanishing before Save**: the "Assign name" input sits inside a panel that's fully
   rebuilt from scratch on every ~700ms poll (to reflect live activity/duration changes) — so
   anything a viewer was mid-typing got silently wiped the moment the next poll landed, discovered
   by actually typing into the field in a live browser session and watching it empty out. Fixed by
   capturing every input's current value immediately before rebuilding the panel and restoring it
   into the newly-rendered element with the same `track_id`.

**Three real bugs were found fixing the activity engine, not caught by the first test-suite run
alone (Phase 5):**
1. **A movement-buffer deadlock**: `_advance()`'s gap-reset branch unconditionally cleared a rule's
   movement-position buffer whenever its raw condition was `False` — but a movement rule's very
   first tick is *always* `False` (it takes two samples to measure movement at all), so the buffer
   was wiped the instant its first sample landed, before a second sample could ever arrive to
   compare against it. CUTTING could never confirm as a result. Found by tracing `rule_since`
   tick-by-tick and seeing it never contain `"CUTTING"`. Fixed by only clearing the confirmation
   streak on a gap, not the position buffer — that buffer already self-prunes via its own time
   window in `_moved_enough`.
2. **A redundant IDLE reset**: a leftover line unconditionally wiped IDLE's entire streak the
   instant any other rule's raw condition became `True`, bypassing IDLE's own grace-period
   tolerance and making a single stray/spurious detection reset an otherwise-solid idle period.
   Fixed by deleting it — `_advance`'s own grace handling already covers this correctly.
3. **Confidence dropped from completed-activity averages**: the same gap-reset branch also cleared
   a rule's collected confidence values whenever it saw `ok=False` — including on the very tick an
   activity's currently-active rule finally goes false, which is the same tick `_complete()` reads
   those values to compute the finished activity's average. The average silently came out as `1.0`
   (an empty-list fallback) instead of the true mean. Fixed by only clearing confidences on a
   genuine post-confirmation gap for a rule that isn't the track's current activity, and by
   recording a confidence as soon as an object is attributed to a person (not gated on the movement
   sub-check also passing) — otherwise a movement rule's first-ever tick permanently lost its
   reading. All three were found using a standalone tick-by-tick tracing script, then confirmed
   fixed against the full `test_activity_engine.py` suite.

**Two real bugs were found this way, not by the unit tests alone (Phases 1-2):**
1. **A concurrency bug**: the manual-reconnect endpoint called `CameraStream.release()` directly
   from the FastAPI request thread while the capture thread could simultaneously be inside
   `cap.read()` on the same OpenCV object — an intermittent crash, caught by a hung test and a
   `py-spy` thread-dump. Fixed by having the API only *signal* the capture thread (a
   `threading.Event`); only that one thread ever touches the OpenCV capture object now. See
   `CameraRunner.reconnect_now`'s docstring.
2. **A deprecation**: this Ultralytics version warns that `predict(half=...)` is deprecated in
   favour of `predict(quantize=...)`. Found by the test suite's own warning output, not by
   anything failing outright. Fixed — the config still calls the setting `half` (matching the term
   used everywhere else in this repo), translated internally to `quantize=16`.

Two things were found to be test-design issues, not product bugs:
- Driving an infinite MJPEG stream through FastAPI's `TestClient` (`httpx`'s ASGI transport) hung
  indefinitely in this environment. The endpoints themselves are fine (proven by the live-server
  tests above); the generator that produces a stream's body is unit-tested directly instead of
  through that path — see `mjpeg_chunks`'s docstring in `backend/api/routes.py`.
- Once several tests loaded real YOLO models in the same process (Phase 3 added a few more), an
  unrelated Phase 1 camera test became occasionally flaky under the resulting system load — a
  single point-in-time status check could catch the camera mid-reconnect. Confirmed reliable in
  isolation; fixed by polling the same way `test_manual_reconnect_endpoint` already did, rather
  than reading connection status once.

**A known cosmetic issue, not a data-correctness one:** when two tracked people's boxes are close
together, their name/ID/confidence/zone labels can visually overlap in the annotated view. The
underlying data is correct either way (confirmed via `/api/detections`); this is purely a label
layout nicety, left for a later pass.

## Training pipeline proof (not real training data)

`garment_cv/training/` generates a synthetic dataset (simple drawn shapes standing in for the 7
classes with no COCO match — a flat rectangle for `fabric`, ruler tick marks for
`measuring_scale`, ...) and fine-tunes a real YOLO weight on it (mAP50 0.977 on a held-out test
split — high because the shapes are trivially easy to tell apart, not evidence of real-world
accuracy), proving the dataset → train → `GARMENT_DETECT_MODEL` swap-in pipeline works end to end
before any real garment-factory data exists. **It is not a step toward real detection** — the
resulting model only recognizes these specific synthetic shapes. Verified against a real live
server, not just a standalone script: `/api/status` and `/api/detections` correctly reflect the
custom model's own 7 classes, and the annotated stream correctly boxes/labels them frame by frame.

Trying this required one real fix: `ObjectDetector` previously rejected *any* model with zero class
overlap with the COCO stand-in vocabulary outright, which is correct for catching an
accidentally-wrong model but also caught this genuinely-different, fully custom one. `start()` now
tells the two cases apart — zero overlap means "a different kind of model", not "misconfigured" —
and a new `custom_model` field in `/api/status` says which mode is actually active.

Full disclosure and how to run it: [`training/README.md`](training/README.md).

## Odoo Manufacturing integration

The camera can drive Odoo 19 work orders: Marking detected starts operation 1, confirmed cutting hands
off to operation 2, and finished cutting closes operation 2 and the MO - with finish rules that a
picked-up pair of scissors or a short pause cannot trigger. Code: `backend/odoo_bridge/`
(`tracker.py` finish rules, `client.py` Odoo JSON-2 API, `bridge.py` queue/reconcile/modes), dashboard
tab **Odoo**, endpoints `/api/odoo/*`. Starts in `off` mode; set it up and roll it out via shadow mode
using **[docs/ODOO_RUNBOOK.md](docs/ODOO_RUNBOOK.md)**. Verified against a real Odoo 19 test database
and a fake-Odoo test suite (`tests/test_odoo_*.py`); not yet verified against live camera footage.

## What comes next (beyond the MVP)

All seven of the spec's own phases (section 21) are built. What's left isn't a skipped MVP step —
it's work the spec itself places after the MVP:
- A real garment-trained detection model (section 7), replacing the COCO stand-in disclosed since
  Phase 2 — needs real production-floor training data the user doesn't have yet. The synthetic
  pipeline above proves the *mechanism* for this is ready; only real data is missing.
- A dedicated frontend build, if the server-rendered `/dashboard` page ever needs to grow beyond
  what plain HTML/JS comfortably supports.
