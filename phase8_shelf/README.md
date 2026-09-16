# Reality Connector — Phase 8: Shelf / Inventory Monitoring

A different kind of watching: not "who is this person," but **"is this shelf
empty."** Matches BRD §8's "Retail: shelf monitoring." A camera dedicated to a
shelf counts stock periodically and raises a restock alert when it runs low —
using the same events database, rule-style alerting, and dashboard as the rest
of the platform.

```
shelf camera → sample every SHELF_CHECK_INTERVAL_S (not every frame - stock doesn't move)
    → count "bottle" detections inside the shelf's region
    → compare to capacity → % stocked
    → below threshold?  → shelf alert (cooldown-limited) → email (optional) → dashboard
```

## Why this is a separate process from person tracking

Bottles don't move between checks and don't need an identity, so none of
Phases 2–3's tracking/recognition machinery applies. Phase 8 runs its own
lightweight loop — `model.predict()` every 30s by default, not 15 fps — against
its **own camera**, and just writes into the same `events.db` (SQLite, WAL)
that the dashboard already reads. The dashboard needs zero code changes to
show shelf data; it only knows about generic `/api/shelves*` endpoints.

## Setup

No new Python dependencies (reuses `ultralytics`, already installed for Phase 2).

**1. Point Phase 8 at the shelf camera.** Same settings as the main camera
(`phase1_camera/.env`), but prefixed `SHELF_`:

```
SHELF_CAMERA_ID=shelf-cam-1
# same shape as the main camera's settings, just SHELF_-prefixed:
SHELF_CAMERA_SOURCE=0                    # webcam/video for testing, or:
SHELF_RTSP_URL=rtsp://user:pass@ip:554/Streaming/Channels/101
SHELF_CHECK_INTERVAL_S=30                # how often to count (seconds)
SHELF_YOLO_CLASSES=39                    # COCO 39 = "bottle" (default)
```

**2. Run it** (separate terminal from the dashboard):
```bash
.venv\Scripts\python.exe -m phase8_shelf.app
```

**3. Add a shelf** — from the dashboard (**▥ Shelves** button) or the API:
```bash
curl -X POST http://127.0.0.1:8000/api/shelves -H "Content-Type: application/json" -d "{
  \"camera_id\": \"shelf-cam-1\", \"name\": \"Aisle 3 - Coke 500ml\",
  \"capacity\": 100, \"low_stock_threshold\": 0.7, \"notify_email\": false
}"
```
Leave `points` out (or don't set it in the dashboard form) to use the **whole
frame** as the shelf region — the right default when a camera is dedicated to
one shelf, which is what "restock below" is checking against.

The dashboard shows a stock bar per shelf (green/amber/red against your
threshold) and a **Shelves need restock** tile, and lists open restock alerts
with ack/close, the same pattern as person alerts.

## Detection: generic "bottle," by design (for now)

This uses YOLO's stock **COCO class 39 ("bottle")** — no training data needed,
works immediately. It counts *any* bottle-shaped object, not specifically your
product, so it's accurate when a shelf holds one product type (which is
exactly the "camera dedicated to a shelf" setup this phase targets) and less
accurate on a mixed shelf or when bottles are packed so tightly they overlap
in the image.

**Two upgrade paths, not built yet:**
- **Custom-trained detector** — fine-tune on real photos of your shelf at
  different stock levels, to distinguish your product from others and count
  dense/overlapping bottles more accurately.
- **Slot-grid comparison** — divide the shelf into a grid and classify each
  slot filled/empty against a reference "full shelf" photo, sidestepping
  overlapping-object counting entirely.

Swapping detectors only touches `counter.py` (`BottleCounter.detect`); the
store, checker, alerting, API, and dashboard don't change.

## Data model

| Table | Holds |
|---|---|
| `shelves` | config: camera, region, product name, capacity, restock threshold, cooldown |
| `shelf_checks` | history: one row per count, with a snapshot |
| `shelf_alerts` | one row per restock alert (open/ack/closed, like person alerts) |

Kept separate from `zones`/`events` — a zone is "is a *person* standing here,"
a shelf is "how many *objects* are stocked here." Same shared `events.db`.

## API (mounted at `/api` by the dashboard)

| Method | Path | |
|---|---|---|
| GET | `/shelves?camera_id=` | list |
| POST | `/shelves` | `{camera_id, name, capacity, points?, product_name?, low_stock_threshold?, notify_email?, cooldown_s?}` |
| PATCH / DELETE | `/shelves/{id}` | |
| GET | `/shelves/summary` | each shelf + its latest check — what the dashboard tile uses |
| GET | `/shelf-checks?shelf_id=&limit=` | history |
| GET | `/shelf-checks/{id}/snapshot` | annotated evidence JPEG |
| GET | `/shelf-alerts?status=` | |
| POST | `/shelf-alerts/{id}` | `{"status"?, "assigned_to"?, "comment"?}` |

## Config (`SHELF_`-prefixed in `.env`)

| Key | Default | |
|---|---|---|
| `SHELF_CAMERA_ID` | `shelf-cam-1` | must match the `camera_id` on the shelves you create |
| `SHELF_CHECK_INTERVAL_S` | `30` | seconds between counts |
| `SHELF_YOLO_MODEL` | `yolo11s.pt` | |
| `SHELF_YOLO_CLASSES` | `39` | COCO class id(s) to count, comma-separated |
| `SHELF_YOLO_CONF` / `SHELF_YOLO_IMGSZ` | `0.35` / `960` | |
| `EVENTS_DB` / `SNAPSHOT_DIR` | shared with Phase 4 | override to run fully standalone |

Per-shelf, not global: `low_stock_threshold` (fraction remaining that triggers
an alert, e.g. `0.7` = alert once 30% has sold), `cooldown_s` (default 1800 —
don't re-alert the same shelf more than once per 30 min), `notify_email`.

## Verified

Store CRUD for shelves/checks/alerts; `BottleCounter` class-filtering (class 39
correctly finds nothing in a bottle-free photo) and zone-restricted counting;
`ShelfChecker` end-to-end (check → alert only when below threshold → cooldown
suppresses immediate re-alerts); a live dashboard run with a second process
writing to the same database, shown on the Shelves panel and stock tile.

## Not yet built

- Multi-shelf-per-camera drawing UI (needs the shelf camera's live frame in
  the dashboard process; today, use the whole frame or set `points` via the API).
- Custom-trained / slot-grid detection (see above).
- Shelf alerts don't yet go through the Phase 6 rule engine (identity/time/zone
  conditions don't apply to "how much stock is left") - they use their own
  fixed per-shelf threshold instead, which is what the use case actually needs.
