"""Colour augmentation for the live-camera dataset: copies of training frames where ONLY the
scissors / cloth objects change colour.

Why this exists: the model has seen exactly two scissors colours (orange, yellow) and two cloth
colours (blue-and-white check, yellow). The user needs it to work on any fabric and any scissors
colour. YOLO's built-in `hsv_h` can't help with that - it *scales* hue values (hue*[1-g, 1+g]), so
orange (hue ~8) barely moves and stays orange. Here the hue of just the object pixels is *rotated*
by a random 35-145 degrees either way, so the same scissors/cloth appear green, purple, red, ...

Only pixels inside a labeled scissors/fabric/fabric_piece box are touched, and within it only those
that are neither the blue table nor skin, so the scene stays realistic (a green pair of scissors on
the same blue table in the same hands) rather than recolouring the whole picture. Labels are
unchanged - the boxes don't move.

What this does NOT do: it can't invent new shapes, textures or materials, and a recoloured copy
isn't a real photo of green scissors. It teaches "colour isn't the cue", which is a cheap, partial
substitute for actually filming other colours - disclosed in training/README.md.

    python -m garment_cv.training.recolour            # adds copies to datasets/live/images/train
"""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
LIVE = HERE / "datasets" / "live"
OBJECT_CLASSES = {1, 2, 4}   # fabric, scissors, fabric_piece (indices in bootstrap_real_labels.CLASSES)


def _object_mask(shape, label_lines: list[str]) -> np.ndarray:
    h, w = shape[:2]
    mask = np.zeros((h, w), bool)
    for line in label_lines:
        parts = line.split()
        if len(parts) != 5 or int(parts[0]) not in OBJECT_CLASSES:
            continue
        cx, cy, bw, bh = (float(v) for v in parts[1:])
        x1, y1 = max(0, int((cx - bw / 2) * w)), max(0, int((cy - bh / 2) * h))
        x2, y2 = min(w, int((cx + bw / 2) * w)), min(h, int((cy + bh / 2) * h))
        mask[y1:y2, x1:x2] = True
    return mask


def recolour(img: np.ndarray, label_lines: list[str], degrees: float) -> np.ndarray | None:
    """Rotate the hue of object pixels by `degrees`. None if the frame has no object boxes."""
    box = _object_mask(img.shape, label_lines)
    if not box.any():
        return None
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[..., 0].astype(int), hsv[..., 1], hsv[..., 2]
    table = (hue >= 96) & (hue <= 120) & (sat > 45)
    skin = ((hue <= 25) | (hue >= 165)) & (sat > 25) & (val < 235) & ((hue >= 165) | (sat < 200))
    coloured = sat > 35                      # greys/whites/blacks have no hue to rotate
    target = box & ~table & ~skin & coloured
    shifted = hsv.copy()
    shifted[..., 0][target] = ((hue[target] + int(degrees / 2)) % 180).astype(np.uint8)   # OpenCV hue is 0-179
    # Overwrite ONLY the recoloured pixels in the original image: converting the whole frame HSV->BGR
    # would nudge every other pixel by ~1 level through rounding, and "everything else is exactly as
    # captured" is the point of doing this per-pixel at all.
    out = img.copy()
    out[target] = cv2.cvtColor(shifted, cv2.COLOR_HSV2BGR)[target]
    return out


def angles(rng: np.random.Generator, n: int) -> list[float]:
    return [float(rng.choice([-1, 1]) * rng.uniform(35, 145)) for _ in range(n)]


def add_copies(dataset: Path, split: str, copies: int, seed: int) -> int:
    rng = np.random.default_rng(seed)
    img_dir, lbl_dir = dataset / "images" / split, dataset / "labels" / split
    made = 0
    for img_path in sorted(img_dir.glob("*.jpg")):
        if "_hue" in img_path.stem:
            continue
        lines = (lbl_dir / (img_path.stem + ".txt")).read_text(encoding="utf-8").splitlines()
        img = cv2.imread(str(img_path))
        for k, deg in enumerate(angles(rng, copies), start=1):
            out = recolour(img, lines, deg)
            if out is None:
                break
            cv2.imwrite(str(img_dir / f"{img_path.stem}_hue{k}.jpg"), out, [cv2.IMWRITE_JPEG_QUALITY, 92])
            (lbl_dir / f"{img_path.stem}_hue{k}.txt").write_text("\n".join(lines), encoding="utf-8")
            made += 1
    return made


def build_recoloured_val(src: Path = LIVE, seed: int = 123) -> Path:
    """A separate evaluation set: the validation frames, recoloured with angles the training copies
    never used (different seed). Not used for training - it measures whether colour changes break
    the model."""
    dst = src.parent / "live_recolour_val"
    for sub in ("images/val", "labels/val"):
        (dst / sub).mkdir(parents=True, exist_ok=True)
        for old in (dst / sub).glob("*"):
            old.unlink()
    rng = np.random.default_rng(seed)
    n = 0
    for img_path in sorted((src / "images" / "val").glob("*.jpg")):
        if "_hue" in img_path.stem:
            continue
        lines = (src / "labels" / "val" / (img_path.stem + ".txt")).read_text(encoding="utf-8").splitlines()
        out = recolour(cv2.imread(str(img_path)), lines, angles(rng, 1)[0])
        if out is None:
            continue
        cv2.imwrite(str(dst / "images" / "val" / img_path.name), out, [cv2.IMWRITE_JPEG_QUALITY, 92])
        (dst / "labels" / "val" / (img_path.stem + ".txt")).write_text("\n".join(lines), encoding="utf-8")
        n += 1
    names = (src.parent / "live.yaml").read_text(encoding="utf-8").split("names:", 1)[1]
    (src.parent / "live_recolour.yaml").write_text(
        f"path: {dst.resolve().as_posix()}\ntrain: images/val\nval: images/val\nnames:{names}", encoding="utf-8")
    print(f"recoloured validation set: {n} frames -> {dst}")
    return dst


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--copies", type=int, default=2)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    print("recoloured training copies added:", add_copies(LIVE, "train", a.copies, a.seed))
    build_recoloured_val()
