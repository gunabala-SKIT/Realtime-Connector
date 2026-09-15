# Reality Connector — Phase 7: Zones

Rules could already say "this camera." Phase 7 adds "**this part of the
frame**" — the "Restricted Zone" half of the BRD's own example (§15):

> IF Unknown Person AND Restricted Zone AND Time 20:00–06:00 THEN Critical Alert

No new process — draw and use zones from the dashboard you already run:
```bash
python -m phase5_dashboard
```

## Draw a zone

1. Open **http://127.0.0.1:8000**, click **▱ Zones**.
2. Click on the frame to place points (3 or more) around the area you care
   about — a doorway, a restricted room, a loading dock.
3. Name it, pick a colour, **Finish & save**.
4. It's immediately live: every new track's position is checked against it.

Click **↻ refresh frame** if the still image is stale (it's a single JPEG
grabbed from the pipeline, not the live stream, so drawing isn't disrupted by
motion). **Undo point** removes the last click; **clear** discards the
in-progress shape. Existing zones are drawn as an overlay so you can see how a
new one relates to them.

## Use it in a rule

Open **⚙ Rules** → the **Zone** dropdown lists every zone you've drawn.
Combine it with the other conditions exactly like the BRD example:

**Zone:** `Server Room`  **Identity:** `UNKNOWN`  **From** `20:00` **To** `06:00`  **Priority:** `critical`

## How zone membership is decided

Each frame, for every tracked person, Reality Connector takes the
**bottom-centre of their bounding box** — the point where they're standing —
and tests it against every enabled zone for that camera with a standard
point-in-polygon check. If they're standing in more than one overlapping zone,
the event records all of them (`"Lobby,Reception"`); a rule's zone condition
matches if its zone name is among them.

Zone corners are stored as **fractions of the frame (0–1)**, not pixels — a
zone drawn once stays correct even if the camera's resolution changes later.

Zone lookups are cached for 5 s per process (zones change rarely; tracks move
every frame), so drawing/editing zones has no per-frame cost.

## API (mounted at `/api` by the dashboard)

| Method | Path | |
|---|---|---|
| GET | `/zones?camera_id=` | list |
| POST | `/zones` | `{camera_id, name, points: [[x,y], ...], color?}` — needs ≥ 3 points |
| PATCH | `/zones/{id}` | update any subset of fields |
| DELETE | `/zones/{id}` | |

`GET /events` and `GET /events/{id}` already include the `zone` field (it's
part of the BRD §16 event model, reserved since Phase 4 and populated from
Phase 7 on).

## How it's wired in (for later phases extending this)

`EventEngine` (Phase 4) gained an optional `zone_resolver(camera_id, bbox,
frame_w, frame_h) -> str | None` attribute — unset by default, so Phase 4 knows
nothing about zones. `phase5_dashboard/server.py` builds a `ZoneIndex` (this
package) and sets `pipeline.engine.zone_resolver = zone_index.resolve`. Same
pattern as the Phase 6 rule-engine hook: each phase only *adds* a wire, nothing
upstream changes.

## Not yet built

- Zone shapes are **polygons only** (a rectangle is just a 4-point polygon —
  no dedicated rectangle/line tool, though the BRD mentions both).
- No "line-crossing" (tripwire) detection, only "inside/outside a region."
- The zone editor draws on a single still frame; if the camera view can change
  (a PTZ) a zone can go stale — fine for a fixed camera, the MVP target.
