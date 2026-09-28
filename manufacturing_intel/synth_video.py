"""Synthetic conveyor video with exact ground truth, for testing and demoing the camera adapter.

    python -m manufacturing_intel.synth_video out.mp4          # writes out.mp4 and out.json

A fixed camera looks down a belt with three station zones. Parts ride in from the left, stop
in each zone for a known time, and ride out to the right. The JSON lists, per part and zone,
when the part's centre was inside the zone (the same definition the adapter uses), so a test
can compare what the adapter recorded with what actually happened.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

W, H, FPS = 640, 360, 10
ZONES = {"S1": (100, 110, 200, 250), "S2": (270, 110, 370, 250), "S3": (440, 110, 540, 250)}  # x1,y1,x2,y2 px
SPEED = 90.0  # px/s along the belt
BELT_Y = 180


def zone_polys_normalised() -> dict[str, list[list[float]]]:
    return {n: [[x1 / W, y1 / H], [x2 / W, y1 / H], [x2 / W, y2 / H], [x1 / W, y2 / H]] for n, (x1, y1, x2, y2) in ZONES.items()}


@dataclass
class PartSpec:
    release_s: float
    dwell: tuple = (4.0, 4.0, 4.0)     # seconds parked in S1, S2, S3
    size: int = 44
    color: tuple = (40, 90, 200)       # BGR
    qr: str | None = None              # draw a QR code with this payload on the part
    edge_jitter: bool = False          # park on the S1 boundary and wobble across it


def _qr_image(text: str, px: int) -> np.ndarray | None:
    try:
        enc = cv2.QRCodeEncoder.create()
        code = enc.encode(text)
    except Exception:  # noqa: BLE001 - encoder not available in this OpenCV build
        return None
    code = cv2.resize(code, (px, px), interpolation=cv2.INTER_NEAREST)
    return cv2.cvtColor(code, cv2.COLOR_GRAY2BGR) if code.ndim == 2 else code


def _path(spec: PartSpec, rng: np.random.Generator) -> list[tuple[float, float, float]]:
    """(t, x, y) samples at FPS for one part, from release until it has left the frame."""
    xs = [-spec.size]
    stops = [(150.0, spec.dwell[0]), (320.0, spec.dwell[1]), (490.0, spec.dwell[2])]
    if spec.edge_jitter:
        stops[0] = (200.0, spec.dwell[0])
    pts: list[tuple[float, float, float]] = []
    t, x = spec.release_s, float(xs[0])
    dt = 1.0 / FPS
    for tx, dwell in stops:
        while x < tx:
            x = min(x + SPEED * dt, tx)
            pts.append((t, x, BELT_Y + rng.normal(0, 0.5)))
            t += dt
        for _ in range(int(round(dwell * FPS))):
            wob = (rng.choice([-3.0, 3.0]) if spec.edge_jitter and tx == 200.0 else rng.normal(0, 0.3))
            pts.append((t, tx + wob, BELT_Y + rng.normal(0, 0.5)))
            t += dt
    while x < W + spec.size:
        x += SPEED * dt
        pts.append((t, x, BELT_Y + rng.normal(0, 0.5)))
        t += dt
    return pts


def background(rng: np.random.Generator) -> np.ndarray:
    img = np.full((H, W, 3), 92, np.uint8)
    img[100:260] = 118                                   # belt
    for x in range(0, W, 40):
        cv2.line(img, (x, 100), (x, 260), (105, 105, 105), 1)
    cv2.rectangle(img, (0, 96), (W, 100), (60, 60, 60), -1)
    cv2.rectangle(img, (0, 260), (W, 264), (60, 60, 60), -1)
    cv2.putText(img, "LINE 1", (10, 340), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (140, 140, 140), 1, cv2.LINE_AA)
    return img


def make_video(path: str | Path, parts: list[PartSpec] | None = None, seed: int = 3, tail_s: float = 3.0) -> dict:
    path = Path(path)
    rng = np.random.default_rng(seed)
    if parts is None:
        parts = [PartSpec(5.0 + 9.0 * k, tuple(float(x) for x in rng.integers(3, 6, size=3)),
                          color=[(40, 90, 200), (60, 170, 60), (180, 90, 40), (40, 170, 200), (150, 50, 150)][k % 5])
                 for k in range(5)]
    paths = [_path(p, rng) for p in parts]
    duration = max(pt[-1][0] for pt in paths) + tail_s
    n_frames = int(duration * FPS)
    bg = background(rng)
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    if not out.isOpened():
        raise RuntimeError("cv2.VideoWriter could not open the output file")
    qr_imgs = [(_qr_image(p.qr, p.size - 8) if p.qr else None) for p in parts]
    truth = [{"release_s": p.release_s, "dwell": list(p.dwell), "zones": {}, "qr": p.qr} for p in parts]
    inside_prev = [{z: False for z in ZONES} for _ in parts]
    for f in range(n_frames):
        t = f / FPS
        frame = bg.copy()
        for i, (spec, pts) in enumerate(zip(parts, paths)):
            idx = int(round((t - spec.release_s) * FPS))
            if idx < 0 or idx >= len(pts):
                continue
            _, x, y = pts[idx]
            half = spec.size // 2
            x1, y1, x2, y2 = int(x - half), int(y - half), int(x + half), int(y + half)
            cv2.rectangle(frame, (x1, y1), (x2, y2), spec.color, -1)
            cv2.rectangle(frame, (x1, y1), (x2, y2), (20, 20, 20), 1)
            if qr_imgs[i] is not None:
                q = qr_imgs[i]
                qx, qy = x1 + 4, y1 + 4
                if qx >= 0 and qy >= 0 and qx + q.shape[1] <= W and qy + q.shape[0] <= H:
                    frame[qy:qy + q.shape[0], qx:qx + q.shape[1]] = q
            for zn, (zx1, zy1, zx2, zy2) in ZONES.items():
                inside = zx1 <= x <= zx2 and zy1 <= y <= zy2
                rec = truth[i]["zones"].setdefault(zn, [])
                if inside and not inside_prev[i][zn]:
                    rec.append([t, None])
                elif not inside and inside_prev[i][zn] and rec and rec[-1][1] is None:
                    rec[-1][1] = t
                inside_prev[i][zn] = inside
        noise = rng.normal(0, 1.5, frame.shape)
        frame = np.clip(frame.astype(np.float32) + noise + rng.normal(0, 0.8), 0, 255).astype(np.uint8)
        out.write(frame)
    out.release()
    meta = {"fps": FPS, "width": W, "height": H, "duration_s": duration, "frames": n_frames,
            "zones": zone_polys_normalised(), "parts": truth}
    path.with_suffix(".json").write_text(json.dumps(meta, indent=1))
    return meta


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "conveyor_demo.mp4"
    m = make_video(target)
    print(f"wrote {target} ({m['duration_s']:.0f}s, {len(m['parts'])} parts) and {Path(target).with_suffix('.json')}")
