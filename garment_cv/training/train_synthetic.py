"""Fine-tune a COCO-pretrained YOLO on the synthetic dataset (`make_synthetic_dataset.py`), then
evaluate it on the held-out test split. Same shape as `phase14_ppe/train.py` elsewhere in this repo.

**This produces a proof-of-concept model, not a usable garment detector** - see
`garment_cv/training/README.md` and `make_synthetic_dataset.py`'s own docstring for why: the
dataset is procedurally-drawn shapes standing in for fabric/tools/machines, not real photos. A high
score here only proves the shapes are easy to tell apart, not that real garment-factory objects
would be.

    python -m garment_cv.training.train_synthetic
    python -m garment_cv.training.train_synthetic --epochs 60

Defaults mirror `phase14_ppe/train.py`'s own GTX 1660 (6 GB) findings: amp=False (half precision
produces NaNs on GTX 16-series), workers=2 (4+ dataloader workers failed to start on Windows there).
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_YAML = HERE / "datasets" / "synthetic.yaml"
MODEL_OUT = HERE.parent / "models" / "synthetic_demo.pt"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="yolo11n.pt", help="COCO-pretrained starting weights")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--workers", type=int, default=2)
    args = ap.parse_args()

    if not DATA_YAML.is_file():
        raise SystemExit(f"{DATA_YAML} not found - run `python -m garment_cv.training.make_synthetic_dataset` first.")

    from ultralytics import YOLO

    model = YOLO(args.model)
    model.train(
        data=str(DATA_YAML), epochs=args.epochs, imgsz=args.imgsz, batch=args.batch, workers=args.workers,
        patience=10, amp=False,  # GTX 16-series produces NaNs under AMP (same reason GARMENT_DETECT_HALF=false)
        project=str(HERE / "runs"), name="train", exist_ok=True, seed=0, plots=True,
    )
    best = Path(model.trainer.best)
    MODEL_OUT.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best, MODEL_OUT)
    print(f"\nbest checkpoint -> {MODEL_OUT}")
    print("Reminder: this model only recognises the synthetic shapes in the training set - see "
         "garment_cv/training/README.md before treating it as anything more than a pipeline proof.")

    # Held-out test split: images the model never saw during training or checkpoint selection.
    metrics = YOLO(str(MODEL_OUT)).val(data=str(DATA_YAML), split="test", imgsz=args.imgsz, batch=args.batch,
                                       project=str(HERE / "runs"), name="test", exist_ok=True, plots=True)
    names = metrics.names
    print("\nTEST SPLIT (held out)   precision  recall  mAP50  mAP50-95")
    for i, c in enumerate(metrics.box.ap_class_index):
        p, r, ap50, ap = metrics.box.class_result(i)
        print(f"  {names[int(c)]:<20} {p:9.3f} {r:7.3f} {ap50:6.3f} {ap:8.3f}")
    print(f"  {'all':<20} {metrics.box.mp:9.3f} {metrics.box.mr:7.3f} {metrics.box.map50:6.3f} {metrics.box.map:8.3f}")


if __name__ == "__main__":  # required on Windows: dataloader workers re-import this module
    main()
