"""Point-in-polygon test. Points and polygons are normalised [0,1] fractions
of the frame, so a zone drawn once stays correct at any stream resolution.
"""
from __future__ import annotations


def point_in_polygon(point: tuple[float, float], polygon: list) -> bool:
    """Standard ray-casting test. `polygon` is a list of [x, y] pairs."""
    x, y = point
    inside = False
    n = len(polygon)
    if n < 3:
        return False
    xj, yj = polygon[-1]
    for xi, yi in polygon:
        if (yi > y) != (yj > y):
            x_intersect = (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
            if x < x_intersect:
                inside = not inside
        xj, yj = xi, yi
    return inside


def ground_point(bbox: tuple[int, int, int, int], frame_w: int, frame_h: int) -> tuple[float, float]:
    """Bottom-centre of a bounding box, normalised - the standard 'where the
    person is standing' point for ground-plane zones."""
    x1, y1, x2, y2 = bbox
    return (((x1 + x2) / 2.0) / max(frame_w, 1), y2 / max(frame_h, 1))


def inflate_polygon(polygon: list, margin: float = 0.08) -> list:
    """Grow a normalised polygon outward from its own centre by `margin`
    (a fraction of its size). Zones are drawn by hand by non-technical staff
    who click roughly, not pixel-perfect, so matching should tolerate a
    person standing right at - or just past - the line they drew, not only
    strictly inside it."""
    if len(polygon) < 3:
        return polygon
    cx = sum(p[0] for p in polygon) / len(polygon)
    cy = sum(p[1] for p in polygon) / len(polygon)
    return [[cx + (x - cx) * (1 + margin), cy + (y - cy) * (1 + margin)] for x, y in polygon]
