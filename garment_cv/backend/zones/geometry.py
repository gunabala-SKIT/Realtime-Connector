"""Pure geometry for zones (Phase 4, spec section 10). No camera, model or storage code here, so
it's trivial to test on its own and to reuse from anything that needs "is this point inside that
polygon" - the resolver, tests, and later phases (activity detection, Phase 5) alike.
"""
from __future__ import annotations

Point = tuple[float, float]


def point_in_polygon(x: float, y: float, polygon: list[Point]) -> bool:
    """Standard ray-casting test. `polygon` is a list of (x, y) points in the same units as
    `x`/`y` - callers decide whether that's pixels or the 0-1 fractions `ZoneStore` persists."""
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y):
            x_at_y = (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
            if x < x_at_y:
                inside = not inside
        j = i
    return inside


def ground_point(box: tuple[float, float, float, float], frame_w: int, frame_h: int) -> Point:
    """Where an object "stands", as a fraction (0-1) of the frame: the bottom-centre of its box.

    Not the box centre - a person's box centre is around their chest, which can sit outside a
    zone drawn around the floor/table area they're actually standing at, while their feet (the
    bottom-centre) are correctly inside it. Applied the same way to every detected class here,
    including static objects like a table, for one consistent rule."""
    x1, y1, x2, y2 = box
    if frame_w <= 0 or frame_h <= 0:
        return 0.0, 0.0
    gx = ((x1 + x2) / 2.0) / frame_w
    gy = y2 / frame_h
    return max(0.0, min(1.0, gx)), max(0.0, min(1.0, gy))
