# Reality Connector — Phase 10: Footfall / Entry-Exit Counting

How many people entered and exited through a doorway — the answer people
actually want when they say "count how many people came in," not "how many
YOLO detections happened" (the same person sitting in frame for 10 seconds
at 10fps is 100 detections, not 100 people).

```
main pipeline's person tracker (already running every frame)
    → every frame: for each configured line, which side is each live
      track's ground point confidently on?
    → side held for confirm_frames in a row AND not within the recross
      cooldown of that track's last counted crossing on this line?
    → log one crossing (in/out, per the line's orientation) → dashboard
```

## The counting problem, and how this avoids it

YOLO re-detects the same person in every single frame. Naively counting
"a person is on side X" per frame, or even per detection, would count one
real visit through a doorway hundreds of times. So this counts **line
crossings by track ID**, not detections or per-frame occupancy:

1. Every frame, compute each live track's ground point (bottom-centre of its
   box - the same convention Phase 7/9 use for "where is this person
   standing").
2. Classify which side of a configured 2-point line that point is on. A
   small **margin** band straddling the line is "too close to call" and
   ignored outright.
3. A side flip only becomes a **confirmed** crossing once it holds for
   `FOOTFALL_CONFIRM_FRAMES` consecutive frames - not on the first frame
   past the margin.
4. Even a confirmed flip is only **counted** (written to the database) if
   it's been at least `FOOTFALL_RECROSS_COOLDOWN_S` since that same track's
   last counted crossing on that line.

Step 4 exists because of something live testing against a real tracker
actually caught: someone standing near the line with natural body sway can
hold a "wrong" side for several consecutive frames by chance, occasionally
enough to pass step 3 on its own - which showed up as ~0.1-0.15s-apart
in/out pairs racking up into the hundreds for one stationary track over a
short test. The margin and frame-debounce reduce how often that happens;
the cooldown is a hard, simple backstop that caps the damage regardless of
tuning, since a real person cannot walk back out through a doorway within
a second or two of having just walked through it. Tested with a synthetic
"5-frame sway, repeated" sequence that reproduces the exact live failure:
without the cooldown it logged one crossing per swing (~19 over 15
simulated seconds); with it, ~7, each still a real, useful count, not a
runaway counter.

## A counting line is not a zone

Deliberately **2 points**, not a 3+ point polygon - a straight line across
a doorway/threshold, not a region. Direction is configurable per line
(`positive_is_in`) since "which way is in" depends on which side of the
line you clicked first; the dashboard draws an arrow on the line showing
the current IN direction, with a **flip direction** button.

## Setup

No new dependencies, no new camera. Optional tuning, in `phase1_camera/.env`:

```
FOOTFALL_LINE_MARGIN=0.03          # normalised distance from the line that's unambiguous
FOOTFALL_CONFIRM_FRAMES=4          # consecutive frames needed before a side-flip counts
FOOTFALL_RECROSS_COOLDOWN_S=2      # min seconds between two counted crossings, same track+line
FOOTFALL_STALE_S=30                # forget a track's state this long after it's not seen
```

**To use it:** open **⇅ Footfall**, click exactly 2 points across the
doorway you want to count, name it, check the drawn arrow points the way
you want "in" to mean (use **flip direction** if not), and **Finish & save**.
Entries/exits/net show live per line, plus **Entries**/**Exits** tiles on
the dashboard header (summed across all lines). **reset** zeroes the
current totals for a line without discarding its crossing history.

**Placement matters** (true of any line-crossing counter, not specific to
this implementation): put the line somewhere people keep moving through -
a narrow doorway/corridor threshold - not somewhere people are likely to
stand still near it. No margin/debounce/cooldown scheme fully substitutes
for people not lingering exactly on the counting line.

## Data model

| Table | Holds |
|---|---|
| `count_lines` | line config: camera, 2 points, direction, `reset_at` |
| `line_crossings` | append-only log: one row per confirmed, counted crossing |

`reset` only moves `reset_at` forward; it never deletes rows, so the full
history is always available via `/api/crossings`.

## API (mounted at `/api` by the dashboard)

| Method | Path | |
|---|---|---|
| GET | `/count-lines?camera_id=` | list |
| POST | `/count-lines` | `{camera_id, name, points: [[x,y],[x,y]], positive_is_in?, color?}` |
| PATCH / DELETE | `/count-lines/{id}` | |
| GET | `/count-lines/summary` | each line + `{entries, exits, net}` since its reset - what the dashboard uses |
| POST | `/count-lines/{id}/reset` | zero current totals, keep history |
| GET | `/crossings?line_id=&limit=` | raw crossing log |

## Verified

Store CRUD; the crossing state machine in isolation (sustained walk-through
→ exactly one crossing; hovering within the margin → nothing; single-frame
jitter → nothing; **the real sway-induced over-count bug found during live
testing, and that the cooldown backstop bounds it** - all with a
deterministically mocked clock); reset semantics; and the full dashboard UI
live (drawing a line via the actual canvas, direction flip, the arrow
rendering, save, reset, delete). Also verified live against a real running
pipeline: before the fix, a stationary tracked person 25s into a run
produced 207-329 phantom crossings; after the fix, the same scenario over a
40s+ window produced exactly 1, and it stayed there.

## Not yet built

- Per-camera / per-line breakdown charts over time (only current totals +
  raw log are surfaced today).
- Automatic daily reset (reset is manual, via the button or the API).
