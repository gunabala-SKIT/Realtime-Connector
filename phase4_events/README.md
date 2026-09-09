# Reality Connector — Phase 4: Event Engine + Backend

The vision pipeline stops being live-only. Identity-state transitions per track
become **deduplicated events** in a database, each with a snapshot, and a small
**FastAPI** service serves them over REST + WebSocket.

```
identity per track (Phase 3)
  → event engine:  one event per (camera, track, identity_status)   ← dedup
  → SQLite events table + snapshot JPEG
  → FastAPI:  GET /events, /stats, /events/{id}/snapshot, WS /ws/events
```

Two processes, one SQLite file (WAL mode — one writer, many readers):

| Process | Command | Role |
|---|---|---|
| pipeline | `python -m phase4_events.app` | camera → person → face → identity → **writes events** |
| API | `python -m phase4_events.api` | **reads events**, serves REST + WebSocket |

## Setup

```bash
.venv\Scripts\python.exe -m pip install -r phase4_events/requirements.txt
```

## Run

Terminal 1 — the pipeline (needs the camera + an enrolled gallery from Phase 3):
```bash
.venv\Scripts\python.exe -m phase4_events.app
```

Terminal 2 — the API:
```bash
.venv\Scripts\python.exe -m phase4_events.api
```
then open **http://127.0.0.1:8000/docs** for the interactive API, or:
```bash
curl http://127.0.0.1:8000/stats
curl "http://127.0.0.1:8000/events?identity_status=UNKNOWN&limit=20"
curl http://127.0.0.1:8000/events/1/snapshot --output ev1.jpg
```

## Events (BRD §16 model)

| Field | Meaning |
|---|---|
| `event_uid` | stable unique id |
| `ts` | event time (unix epoch) |
| `camera_id` | from `CAMERA_ID` in `.env` |
| `track_id` | the BoT-SORT track that triggered it |
| `event_type` | `identity_recognized` \| `identity_unknown` \| `identity_unverified` |
| `identity_status` | `RECOGNIZED` \| `UNKNOWN` \| `UNVERIFIED` |
| `employee_code` / `employee_name` | denormalised — the event is a permanent log entry |
| `confidence` | cosine score |
| `zone` | reserved for Phase 6 |
| `snapshot` | path under `SNAPSHOT_DIR`, annotated JPEG at event time |
| `alert_status` | `none` \| `open` \| `ack` \| `closed` — Phase 7 drives this |

## Deduplication (acceptance doc §7)

**Exactly one event per `(camera_id, track_id, identity_status)`.**

- A person walks in, is recognised → **one** `identity_recognized` event, not one
  per frame.
- Their status changes (UNVERIFIED → RECOGNIZED, or RECOGNIZED → UNKNOWN) → one
  more event for the new status.
- They leave and come back → BoT-SORT gives a new `track_id` → new event.
- A track's dedup memory is cleared `DEDUP_TRACK_TTL_S` seconds after it
  disappears, so a reused `track_id` counts as a fresh person.

By default `UNVERIFIED` does **not** raise an event (it's "we don't know yet").
Set `EMIT_UNVERIFIED_EVENTS=true` to log those too.

## Endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/healthz` | db path, max event id, gallery size |
| GET | `/stats?window=today\|1h\|24h\|7d` | counts by status, open alerts |
| GET | `/events` | filters: `since`, `until` (epoch or ISO), `camera_id`, `identity_status`, `employee_code`, `alert_status`, `limit`, `offset`, `order` |
| GET | `/events/{id}` | one event |
| GET | `/events/{id}/snapshot` | `image/jpeg` |
| POST | `/events/{id}/alert` | `{"status":"open\|ack\|closed"}` |
| GET | `/employees` | enrolled gallery |
| WS | `/ws/events` | streams new events as `{"type":"event","event":{...}}` (~1 s latency) |

## Config (`phase1_camera/.env`)

| Key | Default | |
|---|---|---|
| `CAMERA_ID` | `cam-1` | label on every event |
| `EMIT_UNVERIFIED_EVENTS` | `false` | also log UNVERIFIED tracks |
| `DEDUP_TRACK_TTL_S` | `8` | dedup-memory lifetime after a track vanishes |
| `API_HOST` / `API_PORT` | `127.0.0.1` / `8000` | |
| `EVENTS_DB` | `phase4_events/events.db` | |
| `SNAPSHOT_DIR` | `phase4_events/snapshots` | JPEGs, sub-foldered by date |

## Notes

- **One track = one event per status.** If you see many events for one person in
  a short clip, BoT-SORT is dropping and re-acquiring the track (each new
  `track_id` is a new event by the rule). Raise `track_buffer` in a custom
  `TRACKER` yaml, or the person is genuinely leaving/re-entering.
- The WebSocket polls the DB every ~1 s — fine for a monitor, not sub-second.
  A push notification path is a later optimisation.
- SQLite now, PostgreSQL in Phase 5 with the fuller backend; the store interface
  is small enough to swap.
- Retention / purge (acceptance doc §23) is not implemented yet — the DB and
  snapshot folder grow unbounded.
