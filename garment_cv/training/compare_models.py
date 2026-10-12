"""Compare trained models on the live-camera validation frames, and on recoloured copies of the same
frames (scissors/cloth hue-rotated - see `recolour.py`).

The recoloured set is the closest measurable stand-in for "a colour the model has never seen". It is
not the same as real footage of green scissors - a recoloured photo is still the same shapes, the
same lighting and the same hands - so a good score here is evidence of colour-robustness, not proof.

    python -m garment_cv.training.compare_models garment_cv/models/live_demo_v2.pt garment_cv/models/live_demo.pt
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SETS = {"normal val": HERE / "datasets" / "live.yaml", "recoloured val": HERE / "datasets" / "live_recolour.yaml"}
CLASSES = ("person", "fabric", "scissors", "marking_tool", "fabric_piece")


def main(paths: list[str]) -> None:
    from ultralytics import YOLO

    print(f"{'model':<22}{'set':<16}" + "".join(f"{c:>14}" for c in CLASSES) + f"{'all':>8}   (recall / mAP50)")
    for p in paths:
        model = YOLO(p)
        for label, yaml in SETS.items():
            m = model.val(data=str(yaml), imgsz=1280, batch=4, conf=0.1, verbose=False, plots=False,
                          project=str(HERE / "runs"), name="compare", exist_ok=True)
            per = {m.names[int(c)]: (m.box.class_result(i)[1], m.box.class_result(i)[2])
                   for i, c in enumerate(m.box.ap_class_index)}
            cells = "".join(f"{per[c][0]:>7.2f}/{per[c][1]:<6.2f}" if c in per else f"{'-':>14}" for c in CLASSES)
            print(f"{Path(p).stem:<22}{label:<16}{cells}{m.box.map50:>8.3f}")


if __name__ == "__main__":
    main(sys.argv[1:] or [str(HERE.parent / "models" / "live_demo.pt")])
