"""Train the PPE detector on the Construction-PPE dataset, then evaluate it on the
held-out test split.

    python -m phase14_ppe.train                 # defaults below
    python -m phase14_ppe.train --epochs 80 --model yolo11n.pt

The best checkpoint is copied to phase14_ppe/models/ppe.pt, which is what the
PPE monitor loads (PPE_MODEL in .env overrides it).

Defaults are sized for the GTX 1660 (6 GB) this was trained on, measured:
- yolo11s at batch 16 needs ~7.9 GB -> Windows spills GPU memory into system
  RAM and it drops to ~320 s/batch. Batch 8 fits (4.2 GB) but yolo11s is still
  ~1.8 s/batch (~4.3 min/epoch). yolo11n is ~3x less compute - trainable in
  about an hour, and 3x cheaper to run live next to the person + face models.
- workers=4 never got past start-up here (8 spawned loader processes on
  Windows); 2 workers train normally.
"""
from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DATA_YAML = HERE / "construction-ppe.yaml"
MODEL_OUT = HERE / "models" / "ppe.pt"


def resolved_data_yaml() -> Path:
    """Ultralytics resolves a relative dataset `path` against its own datasets dir,
    not the yaml's folder - so write a copy with the path made absolute."""
    cfg = yaml.safe_load(DATA_YAML.read_text(encoding="utf-8"))
    cfg["path"] = str((HERE / cfg["path"]).resolve())
    out = Path(tempfile.gettempdir()) / "reality-connector-construction-ppe.yaml"
    out.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=str(REPO / "yolo11n.pt"), help="COCO-pretrained starting weights")
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--workers", type=int, default=2)
    args = ap.parse_args()

    from ultralytics import YOLO

    data = resolved_data_yaml()
    model = YOLO(args.model)
    model.train(
        data=str(data), epochs=args.epochs, imgsz=args.imgsz, batch=args.batch, workers=args.workers,
        patience=10, amp=False,  # GTX 16-series produces NaNs under AMP (same reason YOLO_HALF=false)
        project=str(HERE / "runs"), name="train", exist_ok=True, seed=0, plots=True,
    )
    best = Path(model.trainer.best)
    MODEL_OUT.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best, MODEL_OUT)
    print(f"\nbest checkpoint -> {MODEL_OUT}")

    # Held-out test split: images the model never saw during training or model selection.
    metrics = YOLO(str(MODEL_OUT)).val(data=str(data), split="test", imgsz=args.imgsz, batch=args.batch,
                                       project=str(HERE / "runs"), name="test", exist_ok=True, plots=True)
    names = metrics.names
    print("\nTEST SPLIT (held out)   precision  recall  mAP50  mAP50-95")
    for i, c in enumerate(metrics.box.ap_class_index):
        p, r, ap50, ap = metrics.box.class_result(i)
        print(f"  {names[int(c)]:<20} {p:9.3f} {r:7.3f} {ap50:6.3f} {ap:8.3f}")
    print(f"  {'all':<20} {metrics.box.mp:9.3f} {metrics.box.mr:7.3f} {metrics.box.map50:6.3f} {metrics.box.map:8.3f}")


if __name__ == "__main__":  # required on Windows: dataloader workers re-import this module
    main()
