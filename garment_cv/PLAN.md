# Garment Manufacturing Computer Vision System — build plan

Source spec: `garment_product_prompt.md` (kept by the user; section numbers below refer to it).
Separate product from Reality Connector and from `manufacturing_intel/`: own package, own port (8200),
imports nothing from either. Shares only the Python venv.

Per the spec's own development rules (§25) and first task (§26): **build one module at a time, test each
one, stop after each and wait for confirmation before continuing.** This file tracks that order.

## Target architecture (§1)
```
Camera -> Video Stream -> Object Detection -> Object Tracking -> Activity Detection
       -> Zone Detection -> Production Event Engine -> Database -> Dashboard
```

## Target project structure (§24)
```
garment_cv/
  backend/
    api/          FastAPI app and routes, incl. the Phase 7 dashboard page
    cameras/      camera connection, reconnect, metrics
    detection/    object detection, COCO stand-in (see Phase 2 below)
    tracking/     ByteTrack/BoT-SORT identity across frames
    activities/   rule engine: objects + movement + zone + time -> activity
    zones/        per-camera configurable polygons
    events/       activity -> ProductionEvent, persisted + aggregate queries
    workers/      manual track_id -> worker-name assignment (spec §13)
    database/     SQLite schema + thread-safe connection
    main.py       process entrypoint
  frontend/                                                                  - not built; Phase 7's
                                                                               dashboard is server-
                                                                               rendered HTML/JS, the
                                                                               same style every page
                                                                               in this product uses
  models/         trained weights (git-ignored)                             - not built (no custom
                                                                               training yet, §7)
  datasets/       training data (git-ignored)                               - not built
  training/       training scripts                                          - not built
  configs/        .env, zones.json, garment.db
  tests/
```
All seven MVP phases (§21) are now built. `frontend/`, `models/`, `datasets/`, `training/` remain
unbuilt because nothing in the spec's own phase order (§21, §26) calls for them yet - a real
garment-trained model (§7) and a dedicated frontend build are future work beyond this MVP, not
skipped steps within it. Empty folders for them are **not** pre-created — see "Why no empty stub
folders" below.

## Phase order (§21, §26)
1. **Camera module** — done. RTSP/webcam/file source, OpenCV connect, reconnect with backoff,
   FPS/status metrics, live view.
2. **Object detection** — done, as a COCO-pretrained stand-in (user's explicit choice: "start with a
   COCO-class stand-in for now", no real garment training data yet). Only `person`, `scissors` and
   COCO's `dining table` (-> `table`) have a genuine match in the 80-class COCO vocabulary; `fabric`,
   `marking_tool`, `measuring_scale`, `pattern`, `sewing_machine`, `fabric_piece`, `bundle` have none
   and are not produced at all (see `backend/detection/coco_standin.py` - disclosed on the status page
   and in `/api/detections`, the same way Reality Connector's Phase 8 disclosed its bottle-class
   stand-in). Swapping in a real garment-trained model later is a `GARMENT_DETECT_MODEL` config change,
   nothing else - `ObjectDetector` doesn't otherwise know its weights are a stand-in.
3. **Object tracking** — done. Ultralytics implements tracking as a mode of the same detection
   model call (`model.track()` vs `model.predict()`), not an independent stage, so `ObjectDetector`
   (Phase 2's module) is what calls it - see `backend/tracking/config.py`'s docstring for the full
   reasoning. Default tracker is BoT-SORT (camera-motion compensation + re-identification; matches
   the default already used by Reality Connector's own `phase2_detect`), configurable to ByteTrack.
   Verified against a real, moving scene (see `garment_cv/README.md`) that the same real person
   keeps the same ID as their box moves, not just when the scene is static.
4. **Zones** — done. `backend/zones/` is genuinely independent of the camera/detection code (pure
   geometry + a small JSON-file store - no database exists yet, that's Phase 6), unlike tracking:
   checking whether a box's ground point falls in a polygon needs nothing from the model, so this
   phase *does* follow the spec's own folder structure exactly. `ObjectDetector` only reaches it
   through the small `_ZoneLookup` protocol in `detection/detector.py`, so neither module needs to
   import the other's internals. A plain utility page at `/zones` lets an administrator click
   points on the live picture and save/delete zones (spec §10's own words: "the administrator
   should be able to draw zones on the camera view") - not the dashboard, which is Phase 7.
   Verified end-to-end (see `garment_cv/README.md`) that a real detected person is correctly
   placed inside a zone drawn over them, and correctly placed in no zone when they're not.
5. **Activity detection** — done. `backend/activities/` is a rule engine, not a single-frame lookup:
   an activity (`ActivityRule` in `rules.py`) is object + zone + movement + sustained duration,
   exactly the spec's own combination (§8, §11, §22) - it never fires off object presence alone
   (§3: "worker holding scissors ≠ cutting"). Rules are data (a tuple of `ActivityRule` entries),
   not if-statements, per §11's "the rule engine must be configurable rather than hard-coded". Zones
   gained an `activity` field (`zones/store.py`) so a drawn zone says what kind of work happens
   there; `ObjectDetector` reaches the engine only through a small `_ActivityLookup` protocol, same
   decoupling pattern as zones reaching the detector. A person's confirmed activity uses a
   confirm-then-cooldown pattern (streak must hold `_min_s` seconds, survives gaps shorter than
   `grace_s`) - the same shape already proven for PPE compliance and footfall counting elsewhere in
   this repo. Given the COCO stand-in, only **CUTTING** (person + scissors + a "cutting" zone +
   real scissors movement) can actually fire; MARKING/STITCHING/PIECE_HANDLING are fully built and
   fully tested but structurally dormant - their required classes (`marking_tool`, `sewing_machine`,
   `fabric_piece`) don't exist in this model - disclosed via `rules.py`'s docstring and
   `GET /api/activities`'s `reachable_now`/`dormant` fields, the same honesty pattern as Phase 2's
   undetectable-classes list. An **IDLE** activity confirms when nothing else does, so every tracked
   person always has a definite state. Verified against a real, live server (see
   `garment_cv/README.md`) that real tracked people with no scissors present - including one
   standing inside a real "cutting" zone - correctly settle into IDLE rather than any false
   activity; CUTTING itself is verified at the unit/integration level only, since no real photo of
   a person using scissors is available (disclosed, not silently skipped).
6. **Production events + database** — done. `backend/database/` is SQLite, not the spec's own
   "recommended" PostgreSQL (§23) - a single-camera MVP (§20) has no concurrent-writer problem a
   file database can't handle, and swapping to a server-based one later is a connection-string
   change, not a rewrite, since every query goes through this one module. A single shared
   connection guarded by one `threading.Lock` is the exact fix already proven in this repo for the
   same bug class (a shared sqlite3 connection used from multiple threads without synchronization -
   see `backend/database/db.py`'s docstring). Only two tables exist: `production_events` (this
   phase's actual deliverable - §12's Start/End/Duration/Worker/Camera/Zone/Confidence) and
   `workers` (§13's explicit MVP allowance: manual track_id -> name assignment, no face recognition
   required). The much longer entity list in §19 (Camera, ProductionOrder, FabricBatch, Bundle,
   Machine, ...) is that section's own *suggested* full schema, not a Phase 6 requirement -
   building tables nothing yet reads or writes would be speculative, so they're skipped until a
   phase actually needs them, disclosed in `database/db.py`'s own docstring. `ActivityEngine`
   (Phase 5) reaches the database only through a plain `on_complete` callback it was given at
   construction - it never imports `events/` or `database/` itself, the same decoupling pattern as
   every earlier phase's `_ZoneLookup`/`_ActivityLookup` protocols. IDLE completions are persisted
   too, not just the four production stages, since §17's "Idle Time" calculation needs them.
   Verified against a real, live server (see `garment_cv/README.md`) that real completed IDLE
   activities land in the database with correct fields, and that a manually assigned worker name is
   correctly used instead of the `track-{id}` fallback for events recorded after the assignment.
7. **Dashboard** — done. One server-rendered HTML/JS page at `/dashboard` (`backend/api/dashboard.py`),
   the same plain style every earlier phase's page already uses, not a separate frontend build:
   Live Monitoring (raw stream + a client-side canvas overlay reading `/api/detections`,
   `/api/activities` and `/api/zones`, with the five independent on/off toggles §15 asks for -
   drawn client-side, not server-rendered, because `ObjectDetector` renders one shared annotated
   JPEG per cycle for every viewer, so a per-viewer "hide zones" toggle has no way to reach a
   pre-rendered shared stream), Production Timeline (per-worker stage blocks, §16), Activity
   History (a filterable table of persisted events), and Analytics (§17's average-time/idle-time
   figures plus §18's bottleneck comparison - baseline vs. measured only, never a guessed cause).
   Verified against a real, live server and a real browser (see `garment_cv/README.md`) across all
   four tabs with real accumulated data, including assigning a worker name through the live UI and
   watching it propagate into both the live panel and the persisted history.

## Explicit non-goals for this product (from the spec)
- No AI assistant / chatbot / LLM feature of any kind (§ intro).
- Never claim "scissors detected" == "cutting" — activity requires object + movement + zone + time
  together (§8, §22); the dataset must include negative examples (holding tools without using them) (§7).
- Never claim exact piece counts from overlapping/stacked fabric — vision-based estimate + optional
  manual entry, not a precise count (§4).
- Bottleneck detection reports measured numbers only; it does not guess the cause (§18).

## Why no empty stub folders for phases 2–7
The spec's structure (§24) is the *target* end state, not something to scaffold up front — creating empty
`detection/`, `tracking/`, `activities/`, `zones/`, `events/`, `workers/`, `database/`, `frontend/`,
`models/`, `datasets/`, `training/` folders now would look like more got built than actually has. They are
created when their phase starts, matching this document's phase order.

## Status
- Phase 1 (camera module) — done.
- Phase 2 (object detection, COCO stand-in) — done.
- Phase 3 (object tracking, BoT-SORT default) — done.
- Phase 4 (zones, per-camera JSON store + a plain drawing page) — done.
- Phase 5 (activity detection: object + zone + movement + duration rule engine) — done.
- Phase 6 (production events, persisted to a SQLite database) — done.
- Phase 7 (dashboard: live monitoring, timeline, history, analytics/bottlenecks) — done.
- **All seven MVP phases are now built.** 180 automated tests plus real-server verification across
  every phase (including driving the actual `/zones` and `/dashboard` pages through a browser, a
  live activity-engine run against real tracked people, and a real completed activity persisting to
  the database and appearing correctly in the dashboard's Timeline/History/Analytics tabs); see
  `garment_cv/README.md`, "How this was tested".
- What's left beyond the MVP (not started, and not called for by the spec's own phase order): a
  real garment-trained detection model (§7 - the COCO stand-in remains Phase 2's disclosed
  limitation), and a dedicated frontend build (the dashboard is server-rendered HTML/JS, which the
  spec never requires replacing).
