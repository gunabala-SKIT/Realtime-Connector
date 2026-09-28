"""Demo data for the Manufacturing Process Intelligence Platform.

    python -m manufacturing_intel.demo seed            # ~10 days of simulated production history
    python -m manufacturing_intel.demo live            # keep producing in real time (Ctrl+C to stop)
    python -m manufacturing_intel.demo seed --reset    # wipe a DEMO database and start over

Everything is tagged source='simulator' and the dashboard shows a banner while it exists.
Safety: `--reset` refuses to touch a database that has real (non-simulator) events or cameras.

`seed` also adds "Line C (camera demo)": a camera that plays a synthetic conveyor video in a loop
through the real camera adapter, so camera-driven tracking can be seen without any live camera.

Incidents written into the simulation, so there is something to find:
  5 days ago  Line B  CNC Machining runs with 5 of 9 machines 09:00-15:00
  3 days ago  Line A  Grinding rejects ~10% of parts 13:00-17:00 (surface defects)
  2 days ago  Line A  Drilling breakdown 10:00-12:30 (3 of 7 machines, 30% slower)
  today       Line A  Drilling runs 4 of 7 machines, 50% slower, from 3 h ago (a queue builds)
"""
from __future__ import annotations

import argparse
import logging
import random
import signal
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from .blueprint import ROUTES, build_metal_factory
from .config import Settings
from .engine import ProductionEngine
from .simulator import Incident, LineModel, SimEvent, simulate
from .store import Store
from .synth_video import make_video, zone_polys_normalised

log = logging.getLogger("mpi.demo")
SOURCE = "simulator"


def _midnight(ts: float) -> float:
    return datetime.fromtimestamp(ts).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _at(day0: float, hh: float) -> float:
    return (datetime.fromtimestamp(day0) + timedelta(hours=hh)).timestamp()


def line_models() -> dict[str, LineModel]:
    return {
        "Line A": LineModel("Line A", rate_per_h=24.0),
        "Line B": LineModel("Line B", rate_per_h=18.0, mix={"GEAR": 1.0}, slow={"CNC Machining": 1.08}),
    }


def _incidents(day_ago: int, day0: float) -> dict[str, list[Incident]]:
    inc: dict[str, list[Incident]] = {"Line A": [], "Line B": []}
    if day_ago == 5:
        inc["Line B"].append(Incident("CNC Machining", _at(day0, 9), _at(day0, 15), machines=5, note="CNC machines down"))
    if day_ago == 3:
        inc["Line A"].append(Incident("Grinding", _at(day0, 13), _at(day0, 17), reject_p=0.10, note="surface defects"))
    if day_ago == 2:
        inc["Line A"].append(Incident("Drilling", _at(day0, 10), _at(day0, 12.5), machines=3, slow=1.3, note="drilling breakdown"))
    return inc


def _batches(store: Store, ids: dict, ln: str, model: LineModel, start: float, hours: float) -> dict[str, str]:
    out = {}
    for code, share in model.mix.items():
        planned = int(model.rate_per_h * share * hours)
        b = store.add_batch(ids["products"][code], ids["lines"][ln], planned or None, created_at=start)
        out[code] = b["code"]
    return out


def _feed(engine: ProductionEngine, store: Store, ids: dict, ln: str, events: list[SimEvent]) -> dict[str, int]:
    """Push events through the real engine (one transaction). Returns result counts."""
    counts: dict[str, int] = {}
    sts = ids["stations"][ln]
    with store.tx():
        for e in events:
            r = engine.record(e.type, sts[e.station], ts=e.ts, unit_code=e.code, product=e.product,
                              batch=e.batch if e.type == "issue" else None, reason=e.reason, source=SOURCE)
            counts[r["result"]] = counts.get(r["result"], 0) + 1
    return counts


def _next_codes(store: Store) -> dict[str, int]:
    codes: dict[str, int] = {}
    for r in store.q("SELECT p.code pc, COUNT(*) n FROM units u JOIN products p ON p.id=u.product_id GROUP BY p.code"):
        codes[r["pc"]] = r["n"]
    return codes


def reset_demo(store: Store, force: bool = False) -> None:
    real = store.scalar("SELECT COUNT(*) FROM events WHERE source!=?", (SOURCE,), 0)
    cams = store.scalar("SELECT COUNT(*) FROM cameras", (), 0)
    if (real or cams) and not force:
        raise SystemExit(f"Refusing to reset: this database has {real} real (non-simulator) events and {cams} camera(s). "
                         "It isn't a demo database. Use a different --db, or --force-reset if you really mean it.")
    with store.tx() as c:
        for t in ("events", "alerts", "visits", "units", "batches", "route_steps", "routes", "stations",
                  "cameras", "products", "lines", "factories"):
            c.execute(f"DELETE FROM {t}")


def add_camera_demo(store: Store, data_dir: Path | None = None) -> dict:
    """A line watched by one camera whose 'stream' is a looping synthetic conveyor video (parts stop in
    three zones). Everything downstream of the camera is the real adapter and engine."""
    data_dir = data_dir or (Path(__file__).resolve().parent / "data")
    data_dir.mkdir(parents=True, exist_ok=True)
    video = data_dir / "sample_conveyor.mp4"
    if not video.is_file():
        make_video(video)
    line = store.add_line("Line C (camera demo)")["id"]
    cam = store.add_camera("Sample conveyor camera", str(video), line, "motion", {}, False, True)
    prod = store.add_product("PART", "Sample Part", 0.5)["id"]
    raw = store.add_station(line, "Raw Material", "source")["id"]
    zones = zone_polys_normalised()
    sts = [store.add_station(line, f"Station {i}", "process", expected_process_min=0.09, expected_wait_qty=3,
                             camera_id=cam["id"], zone=zones[f"S{i}"])["id"] for i in (1, 2, 3)]
    done = store.add_station(line, "Finished Goods", "sink")["id"]
    store.save_route(line, prod, [raw, *sts, done])
    return {"line": line, "camera": cam["id"], "video": str(video)}


def seed(store: Store, days: int = 10, seed_value: int = 42, now: float | None = None) -> dict:
    now = now or time.time()
    if store.scalar("SELECT COUNT(*) FROM products", (), 0) or store.scalar("SELECT COUNT(*) FROM stations", (), 0):
        raise SystemExit("This database already has a factory configured. Use --reset on a demo database, or a new --db.")
    engine = ProductionEngine(store, clock=lambda: now)
    ids = build_metal_factory(store, lines=("Line A", "Line B"))
    models = line_models()
    rng = random.Random(seed_value)
    codes = _next_codes(store)
    total = {"units": 0, "events": 0}
    today0 = _midnight(now)
    for d in range(days, 0, -1):
        day0 = today0 - d * 86400
        day0 = _midnight(day0 + 3600)        # DST-safe local midnight
        weekend = datetime.fromtimestamp(day0).weekday() == 6
        inc = _incidents(d, day0)
        for ln, model in models.items():
            batch_of = _batches(store, ids, ln, model, _at(day0, 6), 15)
            factor = (0.5 if weekend else 1.0) * rng.uniform(0.92, 1.08)
            events = simulate(model, _at(day0, 6), _at(day0, 21), rng, codes, batch_of, inc[ln], rate_factor=factor)
            events = [e for e in events if e.ts <= now]
            counts = _feed(engine, store, ids, ln, events)
            bad = {k: v for k, v in counts.items() if k != "applied"}
            if bad:
                raise RuntimeError(f"simulator produced events the engine refused ({ln}, day -{d}): {bad}")
            total["events"] += len(events)
            for b in store.list_batches(ids["lines"][ln], "active"):
                store.close_batch(b["id"], ts=_at(day0, 24))
    total["units"] = store.scalar("SELECT COUNT(*) FROM units", (), 0)
    total["camera_demo"] = add_camera_demo(store)
    return total


def live(store: Store, hours: float = 4.0, seed_value: int = 7, stop: threading.Event | None = None) -> None:
    stop = stop or threading.Event()
    now = time.time()
    if not store.list_lines():
        raise SystemExit("No factory in this database - run `seed` first.")
    engine = ProductionEngine(store)
    ids = _ids_from_store(store)
    names = list(line_models())
    if store.scalar("SELECT COUNT(*) FROM units u JOIN lines l ON l.id=u.line_id WHERE l.name IN (%s) AND u.created_at>=?"
                    % ",".join("?" * len(names)), (*names, now - 5 * 3600), 0):
        raise SystemExit("There's already production data from the last 5 hours - the live feed would double up. "
                         "Run `seed --reset` first, or wait.")
    models = line_models()
    rng = random.Random(seed_value)
    codes = _next_codes(store)
    run_from, run_to = now - 5 * 3600, now + hours * 3600
    all_events: list[tuple[SimEvent, str]] = []
    for ln, model in models.items():
        if ln not in ids["lines"]:
            continue
        batch_of = _batches(store, ids, ln, model, run_from, 5 + hours)
        inc = []
        if ln == "Line A":
            inc.append(Incident("Drilling", now - 3 * 3600, now + 2 * 3600, machines=4, slow=1.5, note="drilling problem"))
        evs = simulate(model, run_from, run_to, rng, codes, batch_of, inc)
        all_events += [(e, ln) for e in evs]
    all_events.sort(key=lambda x: x[0].ts)
    past = [(e, ln) for e, ln in all_events if e.ts <= now]
    future = [(e, ln) for e, ln in all_events if e.ts > now]
    with store.tx():
        for e, ln in past:
            engine.record(e.type, ids["stations"][ln][e.station], ts=e.ts, unit_code=e.code, product=e.product,
                          batch=e.batch if e.type == "issue" else None, reason=e.reason, source=SOURCE)
    print(f"loaded {len(past)} events up to now; {len(future)} more will be produced over the next {hours:g} h. Ctrl+C to stop.")
    n = 0
    for e, ln in future:
        while not stop.is_set() and time.time() < e.ts:
            stop.wait(min(1.0, max(0.0, e.ts - time.time())))
        if stop.is_set():
            break
        r = engine.record(e.type, ids["stations"][ln][e.station], ts=e.ts, unit_code=e.code, product=e.product,
                          batch=e.batch if e.type == "issue" else None, reason=e.reason, source=SOURCE)
        n += 1
        if r["result"] != "applied":
            log.warning("live event refused: %s %s", r["result"], r["detail"])
        if n % 200 == 0:
            print(f"  ... {n}/{len(future)} live events")
    print("live feed finished" if not stop.is_set() else "stopped")


def _ids_from_store(store: Store) -> dict:
    lines = {l["name"]: l["id"] for l in store.list_lines()}
    products = {p["code"]: p["id"] for p in store.list_products()}
    stations = {}
    for name, lid in lines.items():
        stations[name] = {s["name"]: s["id"] for s in store.list_stations(lid)}
    return {"lines": lines, "products": products, "stations": stations}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m manufacturing_intel.demo", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed", help="create the reference factory and simulate past days")
    s.add_argument("--days", type=int, default=10)
    s.add_argument("--seed", type=int, default=42)
    s.add_argument("--db", help="database file (default: the product's normal database)")
    s.add_argument("--reset", action="store_true", help="wipe a demo database first (refuses if it holds real data)")
    s.add_argument("--force-reset", action="store_true", help="wipe even if the database holds real data")
    lv = sub.add_parser("live", help="produce events in real time")
    lv.add_argument("--hours", type=float, default=4.0)
    lv.add_argument("--db")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings.load()
    store = Store(args.db or settings.db_path)
    if args.cmd == "seed":
        if args.reset or args.force_reset:
            reset_demo(store, force=args.force_reset)
        t0 = time.time()
        r = seed(store, args.days, args.seed)
        print(f"seeded {r['units']} units / {r['events']} events over {args.days} days in {time.time() - t0:.1f}s -> {store.path}")
        print(f"camera demo line ready (video: {r['camera_demo']['video']}); start the server and it plays in a loop")
    else:
        stop = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        live(store, args.hours, stop=stop)
    return 0


if __name__ == "__main__":
    sys.exit(main())
