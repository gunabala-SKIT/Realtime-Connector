"""Turns the 11 hand-reviewed AI-generated demo images (D:/Realtime-Connector/Images-for_training_models/)
into a YOLO-format dataset. Boxes below were placed by visual inspection of each image, not an
automated labeler - with only 11 images this is a demo-scale dataset, explicitly not the "varied
workers, lighting, angles" real dataset spec section 7 describes. See
`garment_cv/training/README.md`'s "real (AI-generated) demo dataset" section for the full
disclosure: these are AI-generated images of a fictional room, not real garment-factory photos.

    python -m garment_cv.training.prepare_real_dataset
"""
from __future__ import annotations

import shutil
from pathlib import Path

SRC_DIR = Path("D:/Realtime-Connector/Images-for_training_models")
OUT_DIR = Path(__file__).resolve().parent / "datasets" / "real"

CLASS_NAMES: tuple[str, ...] = (
    "person", "fabric", "scissors", "marking_tool", "measuring_scale", "pattern", "fabric_piece",
)
_CLS = {name: i for i, name in enumerate(CLASS_NAMES)}

IMG_W, IMG_H = 1302, 800


def _box(cls: str, x1: float, y1: float, x2: float, y2: float) -> tuple[int, float, float, float, float]:
    """Pixel corners -> YOLO (class_id, cx, cy, w, h), all normalised 0-1."""
    cx, cy = (x1 + x2) / 2 / IMG_W, (y1 + y2) / 2 / IMG_H
    w, h = (x2 - x1) / IMG_W, (y2 - y1) / IMG_H
    return (_CLS[cls], cx, cy, w, h)


# One entry per source image: filename -> list of boxes (pixel coordinates, top-left origin).
LABELS: dict[str, list[tuple[int, float, float, float, float]]] = {
    "fabric.png": [
        _box("fabric", 120, 515, 990, 715),
    ],
    "fabric_2.png": [
        _box("fabric", 545, 430, 975, 600),
        _box("fabric", 120, 555, 990, 715),
    ],
    "fabric_3.png": [
        _box("fabric", 780, 515, 905, 600),
    ],
    "marking_tape.png": [
        _box("measuring_scale", 420, 580, 900, 650),
    ],
    "scissor_and_marker.png": [
        _box("scissors", 555, 580, 820, 645),
        _box("marking_tool", 470, 630, 715, 685),
    ],
    "Scissor_and_oldmarker.png": [
        _box("scissors", 555, 580, 815, 645),
        _box("marking_tool", 565, 635, 650, 675),
    ],
    "cutting_the_fabric.png": [
        _box("person", 388, 280, 665, 800),
        _box("fabric", 570, 460, 1130, 660),
        _box("scissors", 595, 548, 735, 605),
        _box("scissors", 1005, 655, 1040, 710),
        _box("measuring_scale", 660, 418, 745, 460),
        _box("marking_tool", 1050, 700, 1090, 725),
    ],
    "cutting_the_fabric_2.png": [
        _box("person", 722, 155, 1005, 490),
        _box("fabric", 630, 460, 1150, 620),
        _box("scissors", 710, 450, 790, 510),
        _box("scissors", 645, 420, 700, 465),
        _box("measuring_scale", 585, 455, 650, 510),
        _box("measuring_scale", 545, 505, 600, 545),
        _box("pattern", 965, 550, 1110, 710),
    ],
    "holding_scissor.png": [
        _box("person", 775, 125, 1020, 495),
        _box("scissors", 733, 375, 800, 430),
        _box("scissors", 645, 420, 705, 468),
        _box("fabric", 630, 460, 1150, 620),
        _box("measuring_scale", 585, 455, 650, 510),
        _box("measuring_scale", 545, 505, 600, 545),
    ],
    "marking_the_fabric.png": [
        _box("person", 700, 170, 975, 515),
        _box("marking_tool", 670, 460, 710, 510),
        _box("fabric", 630, 460, 1150, 630),
        _box("pattern", 965, 550, 1110, 710),
        _box("scissors", 645, 420, 705, 468),
        _box("measuring_scale", 585, 455, 650, 510),
        _box("measuring_scale", 545, 505, 600, 545),
    ],
    "picking_the_fabric_piece.png": [
        _box("person", 395, 265, 800, 800),
        _box("fabric_piece", 680, 420, 1145, 710),
        _box("scissors", 725, 565, 815, 650),
    ],
}


def main() -> None:
    img_dir, lbl_dir = OUT_DIR / "images" / "train", OUT_DIR / "labels" / "train"
    img_dir.mkdir(parents=True, exist_ok=True)
    lbl_dir.mkdir(parents=True, exist_ok=True)

    for filename, boxes in LABELS.items():
        src = SRC_DIR / filename
        if not src.is_file():
            print(f"WARNING: missing source image {src}, skipping")
            continue
        shutil.copy2(src, img_dir / filename)
        lines = [f"{c} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}" for c, cx, cy, w, h in boxes]
        (lbl_dir / (Path(filename).stem + ".txt")).write_text("\n".join(lines), encoding="utf-8")

    # No real held-out split exists with only 11 images - val/test reuse the same images, which
    # means metrics from this dataset are NOT a genuine generalisation measure, only a sanity
    # check that the model fits what it was shown. Disclosed in training/README.md.
    for split in ("val", "test"):
        (OUT_DIR / "images" / split).mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "labels" / split).mkdir(parents=True, exist_ok=True)
        for img_file in img_dir.glob("*.png"):
            shutil.copy2(img_file, OUT_DIR / "images" / split / img_file.name)
            lbl_file = lbl_dir / (img_file.stem + ".txt")
            shutil.copy2(lbl_file, OUT_DIR / "labels" / split / lbl_file.name)

    yaml_path = OUT_DIR.parent / "real.yaml"
    names_block = "\n".join(f"  {i}: {name}" for i, name in enumerate(CLASS_NAMES))
    yaml_path.write_text(
        f"path: {OUT_DIR.resolve().as_posix()}\n"
        f"train: images/train\nval: images/val\ntest: images/test\n"
        f"names:\n{names_block}\n",
        encoding="utf-8",
    )
    n_boxes = sum(len(b) for b in LABELS.values())
    print(f"Wrote {len(LABELS)} images, {n_boxes} labeled boxes, to {OUT_DIR}")
    print(f"Dataset config: {yaml_path}")


if __name__ == "__main__":
    main()
