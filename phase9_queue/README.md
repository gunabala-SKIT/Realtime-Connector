# Reality Connector — Phase 9: Queue / Crowd Density Monitoring

Count how many people are currently standing inside a zone and alert when
there are more than it can comfortably hold — a checkout line running too
long, a waiting area overcrowded, too many people near a doorway at once.

```
main pipeline's person tracker (already running every frame)
    → every QUEUE_CHECK_INTERVAL_S: for each capacity-configured zone,
      count live tracks whose ground point falls inside it
    → over capacity?  → cooldown-limited alert → dashboard
```

## Why this needs no new process, camera, or model

Unlike Phase 8 (shelf monitoring), a queue is just "how many *people* are in
this *zone* right now" — and the main dashboard's person tracker is already
computing exactly that, every frame, for the whole platform. So Phase 9 is
not a standalone app: it's a small `QueueMonitor` that `phase5_dashboard`
attaches to the running pipeline via `VisionPipeline.frame_hooks`, a generic
"call me every frame with all live positions" hook (`phase4_events/pipeline.py`)
distinct from the per-event `event_hooks` Phase 6 uses. `QueueMonitor` throttles
itself internally (`QUEUE_CHECK_INTERVAL_S`, default 5s) so it doesn't recompute
on every single frame.

## A queue zone is just a zone with a capacity

There's no separate region to draw. Phase 7's `zones` table gained two columns
(`capacity`, `queue_cooldown_s`) — any zone becomes a monitored queue/crowd
zone the moment you give it a capacity; leave it blank and it behaves exactly
as before (just a tag for events/rules, per Phase 7). This means:

- One region can serve double duty — e.g. a "Checkout Line" zone can both tag
  RECOGNIZED/UNKNOWN events (Phase 6 rules) *and* be watched for overcrowding.
- No new drawing UI: the existing Zones panel (▱ Zones) gained two optional
  fields, **Capacity** and **Cooldown (s)**, next to the name/colour fields.

## Setup

No new dependencies, no new `.env` camera settings. Only one optional tuning
knob, in `phase1_camera/.env`:

```
QUEUE_CHECK_INTERVAL_S=5     # how often to recount each queue zone (seconds)
QUEUE_ZONE_MARGIN=0.08       # same "forgive rough drawing" tolerance as Phase 7 zones
```

**To use it:** open **▱ Zones**, draw a zone (or edit an existing one) around
the area you want to watch, and set **Capacity** to how many people it should
comfortably hold. That's it — the zone's row in the panel now shows a live
occupancy bar (`count/capacity`), and a **Zones over capacity** tile appears
on the dashboard header. Alerts show up in the same panel's **Queue / crowd
alerts** list, with ack/close like every other alert type in the system.

## Data model

| Table | Holds |
|---|---|
| `zones.capacity`, `zones.queue_cooldown_s` | opt-in queue config on an existing zone |
| `queue_checks` | history: one row per recount, per zone |
| `queue_alerts` | one row per over-capacity alert (open/ack/closed) |

## API (mounted at `/api` by the dashboard)

| Method | Path | |
|---|---|---|
| GET | `/zones/summary` | every zone + its latest queue check — what the dashboard tile/bar uses |
| GET | `/queue-checks?zone_id=&limit=` | history |
| GET | `/queue-alerts?status=` | |
| POST | `/queue-alerts/{id}` | `{"status"?, "assigned_to"?, "comment"?}` |

`POST`/`PATCH /zones` (Phase 7) now also accept `capacity` and
`queue_cooldown_s`.

## Verified

Store CRUD + migration (existing zones created before this phase load fine,
`capacity=None` by default); `QueueMonitor` counting + cooldown logic in
isolation; a full live run against `people.mp4` (multiple real tracked
people) — correct per-zone counts, an alert firing only when over capacity,
cooldown suppressing an immediate re-alert then firing again once elapsed,
ack/close via the API; the dashboard UI end-to-end (drawing a zone with a
capacity through the actual canvas, the occupancy bar rendering, the tile
updating, ack via a real click).

## Not yet built

- Per-zone occupancy graph over time (only the latest check is surfaced today;
  `queue_checks` already has the history if this gets added later).
- A "sustained overcrowding" mode (alert only if over capacity for N
  consecutive checks, not on a single momentary spike) — today it alerts on
  any single check that's over, subject to cooldown.
