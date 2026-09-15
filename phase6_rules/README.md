# Reality Connector — Phase 6: Rule Engine + Alerting

Events stop being purely a log. A **rule engine** checks every new event and
decides whether it's worth an alert — matching the BRD's example directly
(§15):

> IF Person Detected AND Identity = Unknown AND Camera = Main Entrance
> THEN Create High Priority Alert

There's no separate process to run — Phase 6 is wired into the dashboard.
Just start it as before:
```bash
python -m phase5_dashboard
```
Open **http://127.0.0.1:8000**, click **⚙ Rules** top-right to manage rules.
The first time the rules table is empty, a default rule is seeded:
`"Unknown person - any camera"`, priority **high** — so alerts work out of the box.

## How a rule matches

A rule is a set of conditions **AND**ed together; a blank condition means "any":

| Condition | Example |
|---|---|
| `identity_status` | `UNKNOWN` — leave blank to match `RECOGNIZED` and `UNVERIFIED` too |
| `camera_id` | `entrance` — blank = any camera |
| `time_start` / `time_end` | `20:00` → `06:00` — wraps midnight; blank = any time |
| `days_of_week` | `0,1,2,3,4` (Mon=0..Sun=6) — blank = every day |

When a new event matches an enabled rule, an **alert** is created (BRD §20
Alert entity) with the rule's `priority` (`low`/`medium`/`high`/`critical`).
A per-`(rule, track)` **cooldown** (default 120 s) stops the same situation
re-alerting every frame.

## Notifications (BRD FR-021)

- **Dashboard** — always on. Open-alert count in the tiles, priority badge on
  each event, inline ack/close.
- **Email** — opt-in per rule (`notify_email`). Configure SMTP in `.env`
  (`SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `ALERT_EMAIL_TO`, ...). Sending
  happens on a background thread — a slow or down mail server never stalls
  the vision pipeline; failures are logged, not raised.
- Push / SMS / WhatsApp / webhook are future work (BRD FR-021), same seam:
  add a sender in `notify.py` and a `notify_*` column on the rule.

## API (mounted at `/api` by the dashboard; also on `phase4_events.api` standalone)

| Method | Path | |
|---|---|---|
| GET | `/rules` | list |
| POST | `/rules` | create — `{name, identity_status?, camera_id?, time_start?, time_end?, priority, cooldown_s, notify_email, enabled}` |
| PATCH | `/rules/{id}` | update any subset of fields, e.g. `{"enabled": false}` |
| DELETE | `/rules/{id}` | |
| GET | `/alerts` | filters: `status` (open/ack/closed), `priority` |
| GET | `/alerts/{id}` | |
| POST | `/alerts/{id}` | `{"status"?, "assigned_to"?, "comment"?}` — ack / close / assign / annotate |
| WS | `/ws/alerts` | pushes new alerts as JSON, ~1 s |

`GET /events` now also returns `alert_id` / `alert_priority` per event when one
was raised, and `/stats` `open_alerts` counts from the alerts table.

```bash
curl -X POST http://127.0.0.1:8000/api/rules -H "Content-Type: application/json" -d "{
  \"name\": \"Unknown after hours - entrance\",
  \"identity_status\": \"UNKNOWN\", \"camera_id\": \"entrance\",
  \"time_start\": \"20:00\", \"time_end\": \"06:00\",
  \"priority\": \"critical\", \"notify_email\": true, \"cooldown_s\": 60
}"
```

## How it's wired in (for later phases extending this)

`phase4_events/pipeline.py`'s `VisionPipeline` calls a list of `event_hooks`
with each frame's newly-written events — Phase 4 itself knows nothing about
rules. `phase5_dashboard/server.py` builds a `RuleEngine` and registers
`rule_engine.process_events` as a hook, so Phase 6 only *adds* code; nothing
upstream changed. The standalone `phase4_events.app` CLI viewer does **not**
raise alerts (no hook registered there) — use the dashboard for the full stack.

## Storage

`rules` and `alerts` are two more tables in the same `events.db` SQLite file
(`phase4_events/store.py`) — no new database to run.

## Not yet built (later phases)

- **Zones** (BRD FR-019) — a rule can filter by camera but not by
  region-within-frame yet; that needs Phase 2/3 to tag events with a zone first.
- **OR across conditions** — a rule is pure AND; add several rules for OR.
- Alert **assignment / comments** are in the API and schema but have no
  dashboard UI yet (use `POST /api/alerts/{id}`).
