"""Generates a synthetic, procedurally-drawn dataset standing in for the 7 garment classes that
have no COCO match (`backend/detection/coco_standin.py`'s `NOT_YET_DETECTABLE`): fabric,
marking_tool, measuring_scale, pattern, sewing_machine, fabric_piece, bundle.

**This is explicitly a pipeline proof, not training data** (spec section 7 is clear real training
data must come "from the actual production environment" - none exists yet, which is the whole
reason this product runs on a COCO stand-in in the first place). Each class here is drawn as a
simple, distinct geometric shape/color (a flat rectangle, a dashed-border card, tick marks, ...),
not a photo or anything resembling real fabric, chalk, or a sewing machine. A model trained on this
data will learn to recognize *these specific synthetic shapes* and nothing else - it proves the
dataset -> train -> swap-the-model-in pipeline genuinely works end to end, not that the system can
detect real garment-factory objects. See `garment_cv/training/README.md` for the full disclosure.

    python -m garment_cv.training.make_synthetic_dataset
    python -m garment_cv.training.make_synthetic_dataset --out garment_cv/training/datasets/synthetic --seed 0
"""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

CLASS_NAMES: tuple[str, ...] = (
    "fabric", "marking_tool", "measuring_scale", "pattern", "sewing_machine",
    "fabric_piece", "bundle",
)

# (min_w, max_w, min_h, max_h) in pixels, for a 640x640 canvas - roughly matching each real
# object's relative scale (fabric/sewing_machine large, marking_tool/fabric_piece small).
SIZE_RANGE: dict[str, tuple[int, int, int, int]] = {
    "fabric": (180, 320, 140, 260),
    "marking_tool": (50, 100, 10, 18),
    "measuring_scale": (160, 300, 18, 28),
    "pattern": (120, 220, 100, 180),
    "sewing_machine": (160, 260, 110, 170),
    "fabric_piece": (40, 90, 40, 90),
    "bundle": (90, 160, 70, 130),
}

BRIGHT_COLORS = [(255, 255, 255), (0, 215, 255), (0, 0, 220), (0, 200, 0), (220, 0, 220)]  # BGR
FABRIC_COLORS = [(200, 180, 160), (180, 200, 190), (190, 170, 210), (170, 190, 220), (210, 210, 170)]


def _draw_fabric(canvas, rng, x, y, w, h) -> None:
    color = FABRIC_COLORS[rng.integers(len(FABRIC_COLORS))]
    cv2.rectangle(canvas, (x, y), (x + w, y + h), color, -1)
    darker = tuple(max(0, c - 40) for c in color)
    for _ in range(3):
        yy = y + int(rng.integers(0, h))
        cv2.line(canvas, (x, yy), (x + w, yy), darker, 1)


def _draw_marking_tool(canvas, rng, x, y, w, h) -> None:
    color = BRIGHT_COLORS[rng.integers(len(BRIGHT_COLORS))]
    cv2.rectangle(canvas, (x, y), (x + w, y + h), color, -1)
    cv2.rectangle(canvas, (x, y), (x + w, y + h), (0, 0, 0), 1)


def _draw_measuring_scale(canvas, rng, x, y, w, h) -> None:
    cv2.rectangle(canvas, (x, y), (x + w, y + h), (0, 215, 255), -1)
    cv2.rectangle(canvas, (x, y), (x + w, y + h), (0, 0, 0), 1)
    for tx in range(x, x + w, 10):
        cv2.line(canvas, (tx, y), (tx, y + h // 2), (0, 0, 0), 1)


def _draw_pattern(canvas, rng, x, y, w, h) -> None:
    cv2.rectangle(canvas, (x, y), (x + w, y + h), (230, 230, 230), -1)
    dash, gap, color = 8, 5, (120, 120, 120)
    for px in range(x, x + w, dash + gap):
        cv2.line(canvas, (px, y), (min(px + dash, x + w), y), color, 2)
        cv2.line(canvas, (px, y + h), (min(px + dash, x + w), y + h), color, 2)
    for py in range(y, y + h, dash + gap):
        cv2.line(canvas, (x, py), (x, min(py + dash, y + h)), color, 2)
        cv2.line(canvas, (x + w, py), (x + w, min(py + dash, y + h)), color, 2)


def _draw_sewing_machine(canvas, rng, x, y, w, h) -> None:
    cv2.rectangle(canvas, (x, y + h // 3), (x + w, y + h), (40, 40, 40), -1)
    head_w, head_h = w // 3, h // 3 + h // 3
    cv2.rectangle(canvas, (x, y), (x + head_w, y + head_h), (75, 75, 75), -1)


def _draw_fabric_piece(canvas, rng, x, y, w, h) -> None:
    color = tuple(int(c) for c in rng.integers(60, 230, size=3))
    cx, cy = x + w // 2, y + h // 2
    pts = np.array([
        [x + int(rng.integers(0, w // 3)), y],
        [x + w, y + int(rng.integers(0, h // 3))],
        [x + w - int(rng.integers(0, w // 3)), y + h],
        [x, y + h - int(rng.integers(0, h // 3))],
    ], dtype=np.int32)
    cv2.fillPoly(canvas, [pts], color)


def _draw_bundle(canvas, rng, x, y, w, h) -> None:
    base = tuple(int(c) for c in rng.integers(80, 200, size=3))
    for _ in range(int(rng.integers(3, 6))):
        ox = x + int(rng.integers(0, max(1, w // 3)))
        oy = y + int(rng.integers(0, max(1, h // 3)))
        ow, oh = w // 2, h // 2
        shade = tuple(max(0, min(255, c + int(rng.integers(-20, 20)))) for c in base)
        cv2.rectangle(canvas, (ox, oy), (min(ox + ow, x + w), min(oy + oh, y + h)), shade, -1)
    cv2.line(canvas, (x, y + h // 2), (x + w, y + h // 2), (0, 0, 0), 2)


_DRAW = {
    "fabric": _draw_fabric, "marking_tool": _draw_marking_tool, "measuring_scale": _draw_measuring_scale,
    "pattern": _draw_pattern, "sewing_machine": _draw_sewing_machine, "fabric_piece": _draw_fabric_piece,
    "bundle": _draw_bundle,
}


def make_image(rng: np.random.Generator, imgsz: int = 640, max_objects: int = 4):
    """One synthetic image + its YOLO-format labels: list of (class_id, cx, cy, w, h), all
    normalised 0-1. Objects are placed with simple rejection sampling to keep them mostly
    non-overlapping, not a strict guarantee - a little overlap is fine (and realistic)."""
    bg = tuple(int(c) for c in rng.integers(90, 160, size=3))
    canvas = np.full((imgsz, imgsz, 3), bg, dtype=np.uint8)
    noise = rng.integers(-8, 8, size=canvas.shape, dtype=np.int16)
    canvas = np.clip(canvas.astype(np.int16) + noise, 0, 255).astype(np.uint8)

    n = int(rng.integers(1, max_objects + 1))
    placed: list[tuple[int, int, int, int]] = []
    labels: list[tuple[int, float, float, float, float]] = []
    for _ in range(n):
        cls_id = int(rng.integers(len(CLASS_NAMES)))
        name = CLASS_NAMES[cls_id]
        min_w, max_w, min_h, max_h = SIZE_RANGE[name]
        w = int(rng.integers(min_w, max_w + 1))
        h = int(rng.integers(min_h, max_h + 1))
        w, h = min(w, imgsz - 2), min(h, imgsz - 2)

        box = None
        for _attempt in range(8):
            x = int(rng.integers(0, imgsz - w))
            y = int(rng.integers(0, imgsz - h))
            candidate = (x, y, x + w, y + h)
            if not any(_overlaps(candidate, p) for p in placed):
                box = candidate
                break
        if box is None:
            continue  # couldn't find a non-overlapping spot after 8 tries - skip this one
        x, y, x2, y2 = box
        _DRAW[name](canvas, rng, x, y, x2 - x, y2 - y)
        placed.append(box)
        cx, cy = (x + x2) / 2 / imgsz, (y + y2) / 2 / imgsz
        labels.append((cls_id, cx, cy, (x2 - x) / imgsz, (y2 - y) / imgsz))
    return canvas, labels


def _overlaps(a, b) -> bool:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    if ix1 >= ix2 or iy1 >= iy2:
        return False
    inter = (ix2 - ix1) * (iy2 - iy1)
    a_area = (ax2 - ax1) * (ay2 - ay1)
    return inter > 0.3 * a_area  # allow a little overlap, reject anything mostly covering a's box


def write_split(out_dir: Path, split: str, n: int, seed: int, imgsz: int) -> None:
    img_dir = out_dir / "images" / split
    lbl_dir = out_dir / "labels" / split
    img_dir.mkdir(parents=True, exist_ok=True)
    lbl_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    for i in range(n):
        canvas, labels = make_image(rng, imgsz=imgsz)
        cv2.imwrite(str(img_dir / f"{split}_{i:04d}.jpg"), canvas)
        lines = [f"{c} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}" for c, cx, cy, w, h in labels]
        (lbl_dir / f"{split}_{i:04d}.txt").write_text("\n".join(lines), encoding="utf-8")


def write_dataset_yaml(out_dir: Path) -> Path:
    yaml_path = out_dir.parent / "synthetic.yaml"
    names_block = "\n".join(f"  {i}: {name}" for i, name in enumerate(CLASS_NAMES))
    yaml_path.write_text(
        f"path: {out_dir.resolve().as_posix()}\n"
        f"train: images/train\nval: images/val\ntest: images/test\n"
        f"names:\n{names_block}\n",
        encoding="utf-8",
    )
    return yaml_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "datasets" / "synthetic"))
    ap.add_argument("--n-train", type=int, default=300)
    ap.add_argument("--n-val", type=int, default=60)
    ap.add_argument("--n-test", type=int, default=60)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    out_dir = Path(args.out)
    write_split(out_dir, "train", args.n_train, args.seed, args.imgsz)
    write_split(out_dir, "val", args.n_val, args.seed + 1, args.imgsz)
    write_split(out_dir, "test", args.n_test, args.seed + 2, args.imgsz)
    yaml_path = write_dataset_yaml(out_dir)
    total = args.n_train + args.n_val + args.n_test
    print(f"Wrote {total} synthetic images ({args.n_train} train / {args.n_val} val / {args.n_test} test) "
         f"to {out_dir}")
    print(f"Dataset config: {yaml_path}")


if __name__ == "__main__":
    main()
