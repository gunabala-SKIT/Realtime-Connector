"""Reference factory from the product spec: a metal-parts plant (§3, §13).

Used by the tests and by the demo (`python -m manufacturing_intel.demo`). It only
*configures* a factory - stations, products, routes - exactly what a customer would
enter on the Setup page. Nothing here produces data.

    Gear    Raw -> Cutting -> CNC Machining -> Drilling -> Grinding -> Quality Inspection -> Painting -> Finished
    Shaft   Raw -> Cutting -> Turning -> Drilling -> Polishing -> Finished
    Bracket Raw -> Cutting -> Bending -> Welding -> Painting -> Finished
"""
from __future__ import annotations

from .store import Store

RAW, FINISHED = "Raw Material", "Finished Goods"

# station -> (expected minutes, expected waiting qty, parallel machines/positions).
# Times are the spec's examples (§4-5, §16); machines are sized for ~24 units/hour.
PROCESS_STATIONS = {
    "Cutting": (12, 10, 6), "CNC Machining": (25, 10, 9), "Drilling": (15, 15, 7), "Grinding": (18, 10, 7),
    "Quality Inspection": (8, 10, 3), "Painting": (20, 10, 9), "Turning": (20, 10, 2), "Polishing": (14, 10, 2),
    "Bending": (10, 10, 1), "Welding": (18, 10, 2),
}
ROUTES = {
    "GEAR": [RAW, "Cutting", "CNC Machining", "Drilling", "Grinding", "Quality Inspection", "Painting", FINISHED],
    "SHAFT": [RAW, "Cutting", "Turning", "Drilling", "Polishing", FINISHED],
    "BRACKET": [RAW, "Cutting", "Bending", "Welding", "Painting", FINISHED],
}
PRODUCTS = {"GEAR": ("Metal Gear", 120), "SHAFT": ("Steel Shaft", 90), "BRACKET": ("Bracket", 100)}
TARGET_UNITS_PER_H = 24.0


def build_metal_factory(store: Store, lines: tuple[str, ...] = ("Line A",), products: tuple[str, ...] = ("GEAR", "SHAFT", "BRACKET")) -> dict:
    """Create the reference factory. Returns ids: {'lines': {name: id}, 'products': {code: id},
    'stations': {line: {name: id}}, 'routes': {(line, code): route_id}}."""
    store.ensure_factory("Precision Metal Works")
    out: dict = {"lines": {}, "products": {}, "stations": {}, "routes": {}}
    for code in products:
        name, target = PRODUCTS[code]
        out["products"][code] = store.add_product(code, name, target)["id"]
    for ln in lines:
        line = store.add_line(ln)
        out["lines"][ln] = line["id"]
        needed = {s for code in products for s in ROUTES[code]}
        sts: dict[str, int] = {}
        sts[RAW] = store.add_station(line["id"], RAW, "source")["id"]
        for name, (mins, wait_qty, machines) in PROCESS_STATIONS.items():
            if name in needed:
                sts[name] = store.add_station(
                    line["id"], name, "process", expected_process_min=mins, expected_wait_qty=wait_qty, capacity=machines,
                    target_units_per_h=TARGET_UNITS_PER_H, reject_threshold_pct=5.0, idle_alert_min=25.0)["id"]
        sts[FINISHED] = store.add_station(line["id"], FINISHED, "sink")["id"]
        out["stations"][ln] = sts
        for code in products:
            r = store.save_route(line["id"], out["products"][code], [sts[s] for s in ROUTES[code]])
            out["routes"][(ln, code)] = r["id"]
    return out
