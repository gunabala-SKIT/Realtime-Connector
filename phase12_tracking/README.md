# Reality Connector — Phase 12: Live Employee Tracking (Select & Follow)

Task 3: pick an employee from a dropdown, and watch which camera they're
currently near update live as they walk from one camera's view into
another's — no page refresh, no polling delay on your part.

```
select "Mohanraj"
  → lobby-cam sees + recognizes him   → "Currently on: lobby-cam"   (instant)
  → he walks off; hallway-cam sees him → "Currently on: hallway-cam" (instant)
```

## Builds directly on Phase 11 - and reuses the live feed you already have

Like Phase 11, no new package of detection code: cross-camera identity is
already solved by face recognition, and every camera already writes
RECOGNIZED events (employee_code, camera_id, timestamp) into the same
shared `events.db`. The one new thing Phase 12 needed was **live push**
instead of "load once and look at a static number" - and it turns out the
dashboard already had exactly that: `/ws/events` (built for the live event
feed and alert sound) **polls the database every ~1 second** and pushes any
new rows to every connected browser. It doesn't matter which process wrote
the row - a live camera process, or (as tested) a direct database write -
the poll picks it up and broadcasts it regardless. The frontend just needed
one more filter: if the pushed event matches the currently-tracked
employee's code and is RECOGNIZED, refresh the tracking view.

## What it shows

- **Currently on: `<camera_id>`** (or **Last seen** if the most recent
  sighting is more than 5 minutes old - probably out of any camera's view
  by now), with a relative time ("34s ago") and a live/stale badge.
- **Recent path** - every sighting for that employee today, most-recent
  first, letting you see the sequence of cameras they've passed through.

**Scope note:** this dashboard only streams *its own* camera's live video
(`/live.mjpg`) - in a real multi-camera deployment each camera is its own
process/port (see [phase11_attendance/README.md](../phase11_attendance/README.md)'s
`GALLERY_CHROMA_URL` note), so there's no single dashboard with every
camera's video feed to switch between. "Currently on: X" identifies the
camera by name/label, not by showing you that camera's video inline. A
unified multi-stream view would be a separate, larger piece of work.

## API (mounted at `/api` by the dashboard)

| Method | Path | |
|---|---|---|
| GET | `/track/{employee_code}?date=&limit=` | `{current_camera, last_seen_ts, path: [...]}`, defaults to today, most-recent-first |

Reuses the existing `GET /employees` (for the dropdown) and `/ws/events`
(for live push) - no new WebSocket endpoint.

## Verified

Store/API: current_camera always reflects the single most recent sighting
regardless of arrival order (an out-of-order older event never overwrites a
newer current_camera); path ordering and limit; an employee never seen
today returns an empty result, not an error; date scoping isolates one day.

**Live, end to end, including the push mechanism itself**: with the
dashboard open and an employee selected in the browser, a new RECOGNIZED
sighting written directly into the live database (simulating a different
camera recognizing them, since the test footage isn't the real enrolled
employee) appeared in the UI within ~1-3 seconds with **zero manual
action** - no button click, no refresh - confirmed twice, including a
follow-up older/out-of-order sighting correctly *not* overriding the
already-current camera, and correctly appearing in the path history behind
it.

## Not yet built

- Vehicle entry/exit tracking (Task 2) - independent of this, needs
  license-plate recognition as vehicles have no equivalent to face
  recognition today.
- A unified multi-camera live video view (see scope note above).
