# Reality Connector — Phase 11: Cross-Camera Employee Attendance

"Employee X entered the company at 9 and exited at 7 today" — even when the
entry was seen on one camera and the exit on a completely different one.

```
front-door camera  → RECOGNIZED Mohanraj at 09:03  ┐
                                                     ├─ same events.db →  attendance:
back-door camera   → RECOGNIZED Mohanraj at 18:47  ┘                    Mohanraj  in 09:03 (front-door)
                                                                                   out 18:47 (back-door)
```

## No new package - this is pure aggregation over data Phase 4 already writes

Unlike Phases 7-10, there's no `phase11_attendance/*.py` — every camera
process already writes RECOGNIZED events (with `employee_code`, `camera_id`,
timestamp) into the same shared `events.db` the moment Phase 3's face
recognition confirms someone's identity, on *whichever* camera saw them.
Cross-camera identity is already solved by face recognition; the only thing
missing was turning "a pile of timestamped sightings across N cameras" into
"this person's entry and exit time" - a reporting feature, added directly to
`phase4_events/store.py` + `api.py` and the dashboard, with no new detection
logic and no per-frame hook.

**This only works for enrolled employees whose face gets recognized** - it
has no way to track an anonymous visitor across cameras (that would need
visual re-identification, a genuinely different and much harder problem).

## Camera roles - the one new concept

A camera only contributes to attendance once it's tagged with a **role**:

| Role | Meaning |
|---|---|
| `entry` | a RECOGNIZED sighting here can set someone's *entry* time (earliest wins) |
| `exit` | a RECOGNIZED sighting here can set someone's *exit* time (latest wins) |
| `both` | one camera watching a single door that people both enter and leave through |
| *(untagged, or `internal`)* | ignored entirely - an office/internal camera isn't a doorway |

Tag cameras from the dashboard's **🕒 Attendance** panel — no restart needed,
takes effect on the next `/api/attendance` query.

## Setup

Nothing to configure to turn this on — it works as soon as at least one
camera is tagged `entry` (or `both`) and Phase 3 face recognition is running
on it, same as every other camera in this project. For a *real* multi-camera
deployment (one process per camera, each on its own machine or port), each
process needs to share **one** face gallery, which means switching from
Phase 3's default embedded-per-process ChromaDB to its server mode:

```
chroma run --path phase3_identity/gallery_chroma --port 8600
```
then, in every camera process's `.env`:
```
GALLERY_CHROMA_URL=localhost:8600
```
(`GALLERY_DIR`/embedded mode is fine for a single camera; it is **not**
safe for two processes to open the same embedded directory at once.)

## API (mounted at `/api` by the dashboard)

| Method | Path | |
|---|---|---|
| GET | `/camera-roles` | `{roles: {camera_id: role}, known_camera_ids: [...]}` |
| POST | `/camera-roles` | `{camera_id, role: "entry"\|"exit"\|"both"\|"internal"}` |
| DELETE | `/camera-roles/{camera_id}` | |
| GET | `/attendance?date=YYYY-MM-DD` | per-employee entry/exit across every camera; defaults to today |

## Verified

Store-level: cross-camera entry+exit aggregation for the exact scenario
asked for (entry cam A, exit cam B); entry always the *earliest* entry-role
sighting and exit the *latest* exit-role sighting even with several
sightings through the day; an `internal`/untagged camera never contributing;
an employee with only an entry (still present) correctly shows no exit; a
single `both`-role camera supplying both; day-boundary correctness (23:59
today vs 00:01 tomorrow land on the right day). API-level: the same via
FastAPI's TestClient, plus validation (bad role, bad date format).

**Live, with two genuinely separate dashboard processes** (`front-door` on
one port, `back-door` on another), each running its own full pipeline
(YOLO + face recognition), sharing one `events.db` **and**, for the first
time in this project, one Chroma **server** gallery (`chroma run` +
`GALLERY_CHROMA_URL`) instead of the single-process embedded mode - both
processes correctly loaded the same 2 real enrolled employees. Since the
stock test-footage used for each camera doesn't show the real enrolled
employee, a realistic pair of RECOGNIZED events (front-door 09:03, back-door
18:47, for the real employee "Mohanraj") was inserted directly into the
shared, live database to complete the cross-camera scenario - and both
running processes' `/api/attendance` immediately and correctly returned the
combined entry/exit record, confirmed again in the dashboard UI (camera-role
dropdown live-editing, the attendance list, the "present" badge for an
entry-only employee, and the "in building now" tile).

## Not yet built

- Historical attendance browsing beyond one date at a time (no calendar/
  range view yet - `GET /attendance?date=` is called once per day picked).
- Task 3 (live "select an employee and follow them across cameras in real
  time") builds directly on this phase's cross-camera identity data.
