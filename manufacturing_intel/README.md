# Manufacturing Process Intelligence Platform

Turns physical movement on a production line into production intelligence: where every product is,
how long each stage takes, where products wait, where the bottleneck is, what gets rejected, and how today
compares with yesterday. Built from `manufacturing_process_intelligence_product.md`; the plan and the
section-by-section mapping are in [`PLAN.md`](PLAN.md).

It is a **separate product** from Reality Connector: own package, own database, own port, own dashboard. It
imports nothing from the `phase*` packages and shares only the Python environment.

```
python -m manufacturing_intel                 # dashboard + API on http://127.0.0.1:8100
python -m manufacturing_intel.demo seed       # optional: reference factory + 10 days of simulated history
python -m manufacturing_intel.demo live       # optional: keep producing simulated events in real time
python -m unittest discover -s manufacturing_intel/tests -t .     # 115 tests, ~3 min (the camera tests play video)
```

## Try it without any camera

```
python -m manufacturing_intel.demo seed        # Line A / Line B (simulated), Line C (camera demo)
python -m manufacturing_intel.demo live        # in a second terminal
python -m manufacturing_intel
```

* **Line A and Line B** are simulated (a banner says so on every page). Incidents are written into the data so
  there is something to find: Drilling breakdown 2 days ago, a Grinding quality problem 3 days ago, CNC down on
  Line B 5 days ago, and a Drilling slowdown building a queue right now.
* **Line C (camera demo)** is real pipeline, synthetic picture: a video of parts riding a belt and stopping in
  three station zones plays in a loop and goes through the actual camera adapter and engine. Cameras → Sample
  conveyor camera shows the live view with detections; the Line C numbers are produced from that video.
* To use your own video, add a camera (Cameras → Add) with the file path as the source, draw a zone per station
  on the picture, and build a route in Setup. An RTSP address or USB device number works the same way.

## How it works

Everything that happens is an **event** posted to one engine. Cameras are only one source.

| event | meaning |
|---|---|
| `issue` (or `enter` at the source station) | raw material released → a unit is created |
| `enter` at a process station | processing starts |
| `exit` at a process station | processing ends; the unit now *waits* for the next station |
| `reject` (+ reason) | unit scrapped at that station |
| `enter` at the sink | unit finished (automatic when the sink has no camera) |

processing time = exit − enter at a station · waiting time = enter at a station − exit of the previous one.

**Identity.** If the source knows the unit (barcode/QR/PLC) the event carries `unit_code` and the unit's full
journey is exact. Without a code the line is matched **FIFO** - a documented assumption. The engine never
repairs anything silently: a duplicate is *ignored*, an event with nobody to match is *unmatched*, a step that
was never reported is marked *skipped* with no times invented, and every result is kept in the audit log
(Operator → Recent events) and summarised as a data-quality line on the Overview.

### Camera adapter

* `motion` (default): learns the empty scene, then treats anything that isn't background as a product - including
  one parked in a machine for twenty minutes (ordinary motion detection would absorb it and report a false
  exit). Keep the belt clear while it learns (first 3 s, or Cameras → Recalibrate). Colour-based, so a
  green part on a grey belt is seen. Touching parts merge into one blob.
* `yolo`: Ultralytics detection + tracking with the classes/weights in the camera's params.
* Events are debounced and released in time order; a part jittering across a zone edge is one visit.
  Exit reuses the unit the enter resolved to. A readable QR code on the product becomes its unit code.

### Analytics definitions

| | |
|---|---|
| entered / completed / rejected | arrived at / finished processing at / scrapped at a station in the period |
| waiting / processing | units at the station now (or at the period's end) |
| throughput | completed per *active* hour (first to last event on the line in the period, ≥ 15 min) |
| utilisation | machine-time in use ÷ (machines × active hours); set **Machines** per station in Setup |
| yield | finished ÷ (finished + rejected). The spec's simple "finished ÷ started" is shown too but counts units still on the floor as losses |
| production time | finished − started, per unit finished in the period; compared with the product's target |
| comparison | *like for like*: a period still in progress is compared with the previous one cut at the same time of day |
| bottleneck | queue in front of a station above its expected level (highest ratio wins); "potential" when the queue is ≥ 60 % of its limit and the station clears < 80 % of what arrives. **Observations only - no cause is guessed** |

### Alerts

Bottleneck · production delay · machine idle · quality · low production. One open alert per (kind, station),
re-checked every 30 s, self-resolving, each carrying its evidence numbers. Thresholds are per station (Setup →
Stations). *Idle* means work is waiting, nothing is being processed and nothing has happened for N minutes; a
quiet station with nothing waiting is not "idle". *Low production* only fires when work was available.

### Assistant

A deterministic router over the analytics (no LLM): answers only from the data, says which line and period it
assumed, and for "why" questions lists what changed (queue, processing/waiting time, rejects) ranked by
impact - it does not invent causes. Unmatched questions get a list of what it can answer.

## Sending events from other systems

```
curl -X POST http://127.0.0.1:8100/api/ingest/event -H "Content-Type: application/json" \
     -d '{"type":"enter","line_id":1,"station":"Drilling","unit_code":"GEAR-000125"}'
```
`POST /api/ingest/events` takes up to 500. From another machine set `MPI_API_KEY` on the server and send
`X-API-Key`. Full examples: Setup → Integration.

## Security defaults

Listens on `127.0.0.1` only. Reads are open to anyone who can reach the port; configuration changes need a local
client (`MPI_ADMIN_REMOTE=true` to allow others); ingest from other machines needs `MPI_API_KEY`. Camera URLs
are shown with the password masked. There are no user accounts yet - put it behind your own reverse proxy /
VPN before exposing it beyond a trusted network.

## Settings (env or `manufacturing_intel/.env`)

`MPI_DB`, `MPI_HOST`, `MPI_PORT`, `MPI_API_KEY`, `MPI_ADMIN_REMOTE`, `MPI_ALERT_INTERVAL_S` - see `config.py`.

## What's deliberately not here yet

Forecasting, anomaly detection, predictive maintenance, schedule optimisation (spec §25), PostgreSQL (SQLite in
WAL mode is used; several processes can write), PLC/OPC-UA connectors (use the ingest API), user accounts and
roles, rework loops (an event that goes backwards in the route is flagged, not looped), a product-specific
trained detector (the detector is pluggable; only background-reference and generic YOLO ship).

## Layout

`store.py` schema/CRUD · `engine.py` event → unit journeys · `analytics.py` KPIs/flow/bottleneck/compare ·
`periods.py` windows & shifts · `alerts.py` · `assistant.py` · `vision.py` camera adapter · `server.py` API ·
`web/` dashboard · `simulator.py` + `demo.py` demo data · `synth_video.py` test/demo video with ground truth ·
`blueprint.py` reference factory · `tests/`.
