"""Fine-tune on the 11-image real (AI-generated) demo dataset (`prepare_real_dataset.py`).

**This is a demo-scale dataset, not production training data** - 11 images, hand-labeled, no
genuine held-out validation split (val/test reuse the same 11 training images, since there aren't
enough to hold any back). Metrics this prints are a sanity check that the model fits what it was
shown, not a measure of real-world generalisation. See `garment_cv/training/README.md`.

    python -m garment_cv.training.prepare_real_dataset   # build the dataset first
    python -m garment_cv.training.train_real
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_YAML = HERE / "datasets" / "real.yaml"
MODEL_OUT = HERE.parent / "models" / "real_demo.pt"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="yolo11n.pt")
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=4)  # small on purpose - only 11 training images
    ap.add_argument("--workers", type=int, default=2)
    args = ap.parse_args()

    if not DATA_YAML.is_file():
        raise SystemExit(f"{DATA_YAML} not found - run `python -m garment_cv.training.prepare_real_dataset` first.")

    from ultralytics import YOLO

    model = YOLO(args.model)
    model.train(
        data=str(DATA_YAML), epochs=args.epochs, imgsz=args.imgsz, batch=args.batch, workers=args.workers,
        patience=30, amp=False,  # GTX 16-series produces NaNs under AMP
        project=str(HERE / "runs"), name="train_real", exist_ok=True, seed=0, plots=True,
    )
    best = Path(model.trainer.best)
    MODEL_OUT.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best, MODEL_OUT)
    print(f"\nbest checkpoint -> {MODEL_OUT}")
    print("Reminder: trained on 11 AI-generated demo images of a room that isn't your real camera's "
         "room - test against real footage before trusting any detection from this model.")

    metrics = YOLO(str(MODEL_OUT)).val(data=str(DATA_YAML), split="test", imgsz=args.imgsz, batch=args.batch,
                                       project=str(HERE / "runs"), name="test_real", exist_ok=True, plots=True)
    names = metrics.names
    print("\nTEST SPLIT (same 11 images - NOT held-out)   precision  recall  mAP50  mAP50-95")
    for i, c in enumerate(metrics.box.ap_class_index):
        p, r, ap50, ap = metrics.box.class_result(i)
        print(f"  {names[int(c)]:<16} {p:9.3f} {r:7.3f} {ap50:6.3f} {ap:8.3f}")
    print(f"  {'all':<16} {metrics.box.mp:9.3f} {metrics.box.mr:7.3f} {metrics.box.map50:6.3f} {metrics.box.map:8.3f}")


if __name__ == "__main__":
    main()
