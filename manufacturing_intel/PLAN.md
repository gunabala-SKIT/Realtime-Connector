# Manufacturing Process Intelligence Platform — implementation plan

Source spec: `manufacturing_process_intelligence_product.md` (kept by the user; section numbers below refer to it).
This is a **separate product** from Reality Connector: own package, own SQLite database
(`manufacturing_intel/data/mpi.db`), own port (8100), own dashboard, own process. It imports
nothing from `phase*` packages — only the shared Python venv is reused.

## Core idea (§26)
movement → **events** → production data → time analysis → bottlenecks → KPIs → AI answers.
Cameras are only one *source* of events. Everything else (barcode scanners, PLCs, IoT, operators)
posts the same three events to one engine, so the product works with or without cameras.

## Event model
| event | meaning |
|---|---|
| `issue` / `enter` at a **source** station | raw material released into the line → a unit is created |
| `enter` at a **process** station | processing starts (unit enters the machine zone) |
| `exit` at a **process** station | processing ends; the unit now *waits* for the next station |
| `reject` | unit scrapped at that station (with a reason) |
| `enter` at a **sink** station | unit finished (or automatic when the sink has no camera/feed) |

* processing time = exit − enter at a station; waiting time = enter at station − exit of the previous one (§7).
* Events carry a unit code when the source knows it (barcode/QR/PLC) → full per-unit journey (§6).
* Camera-only sources have no unit identity across cameras → **FIFO matching** on the line
  (documented assumption; unmatched/out-of-route events are logged and surfaced as data-quality
  counters, never silently repaired).
* Every event is stored in an append-only `events` audit log (§22).

## Data model (§22)
factory → line → **stations** (physical/camera-monitored places, reusable) → **routes** (product + ordered
steps over stations, so shared stations work: §13) → batches → units → **visits** (one row per unit per
station: arrived / start / end / outcome / reject reason).

## Analytics (§8–10, 15, 17–18, 23)
Per station: entered, completed, rejected, waiting, processing, avg processing, avg waiting, throughput,
efficiency, utilisation, peak queue, status. Per line: issued / WIP / finished / rejected, cycle time vs
target, on-time %, yield, bottleneck (with evidence, not causes), period comparison (like-for-like),
breakdowns day → shift → line → product → batch → unit.

## Alerts (§16)
bottleneck · production delay · machine idle · quality threshold · low production. One open alert per
(kind, station); auto-resolves when the condition clears; every alert carries its evidence numbers.

## AI assistant (§19–20)
MVP = deterministic intent router over the analytics engine (no LLM): answers only from data, quotes
the numbers it used, and for "why" questions reports observed changes rather than inventing causes.
An LLM can later sit in front of the same analytics functions.

## Delivery order
1. store + engine + tests   2. analytics + alerts + tests   3. factory simulator (demo data + live feed)
4. API + dashboard (Overview, Batches, Units, Analytics, Alerts, Assistant, Operator, Setup)
5. camera adapter (motion or YOLO detection, zones per station, QR) + synthetic conveyor video test
6. README, packaging notes

## Deliberately deferred (§25 and infrastructure)
Forecasting, anomaly detection, predictive maintenance, schedule optimisation, PostgreSQL, PLC/OPC-UA
connectors (use the ingest API), multi-user auth/roles, rework loops (out-of-route events are flagged),
custom-trained product detector (the detector is pluggable; MVP ships motion + generic YOLO).
