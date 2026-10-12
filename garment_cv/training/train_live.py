"""Fine-tune on the real footage captured from the real camera (`bootstrap_real_labels.py`).

Unlike `train_real.py` (AI-generated images of a room that doesn't exist), this is the actual
camera, room, table and lighting the live demo runs in - the one thing that earlier attempt lacked.
It is still a small, single-session, single-person dataset (see `training/README.md`).

Differences from the earlier scripts, each there for a reason found the hard way:
- imgsz 1280, not 640: the scissors and pen are ~60-150 px wide in a 2560-wide frame, so at 640
  they shrink to 15-40 px, too small to learn reliably.
- --recolour 2: the model has seen only two scissors colours and two cloth colours, but users cut any
  fabric with any scissors. Copies with just those objects recoloured teach "colour isn't the cue"
  (see `recolour.py` - a partial substitute for actually filming other colours, not a replacement).
- freeze=10: fine-tuning all layers on a few dozen images made the AI-image model forget how to
  detect even a clearly visible person (the base COCO model found them, the fine-tuned one found
  nothing). Freezing the early backbone layers keeps the general features COCO already learned.

    python -m garment_cv.training.bootstrap_real_labels   # build the dataset first
    python -m garment_cv.training.train_live
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_YAML = HERE / "datasets" / "live.yaml"
MODEL_OUT = HERE.parent / "models" / "live_demo.pt"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="yolo11n.pt")
    ap.add_argument("--epochs", type=int, default=35)
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--freeze", type=int, default=10)
    ap.add_argument("--recolour", type=int, default=2,
                    help="recoloured copies of each training frame (scissors/cloth only) - 0 to disable")
    args = ap.parse_args()

    if not DATA_YAML.is_file():
        raise SystemExit(f"{DATA_YAML} not found - run `python -m garment_cv.training.bootstrap_real_labels` first.")

    from ultralytics import YOLO

    from . import recolour
    if args.recolour > 0:
        # the dataset builder wipes the folders, so copies are made fresh each run; the validation
        # frames are never recoloured, and a separate recoloured copy of them is built for evaluation
        made = recolour.add_copies(recolour.LIVE, "train", args.recolour, seed=7)
        print(f"added {made} recoloured training copies (scissors/cloth hue-rotated, scene untouched)")
    recolour.build_recoloured_val()

    model = YOLO(args.model)
    model.train(
        data=str(DATA_YAML), epochs=args.epochs, imgsz=args.imgsz, batch=args.batch, workers=args.workers,
        freeze=args.freeze, patience=30, amp=False,  # GTX 16-series produces NaNs under AMP
        project=str(HERE / "runs"), name="train_live", exist_ok=True, seed=0, plots=True,
    )
    best = Path(model.trainer.best)
    MODEL_OUT.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best, MODEL_OUT)
    print(f"\nbest checkpoint -> {MODEL_OUT}")
    print("Reminder: the validation split is the last ~30% (in time) of each clip from the SAME session, "
         "person and table - optimistic. The real test is fresh footage captured after training.")

    metrics = YOLO(str(MODEL_OUT)).val(data=str(DATA_YAML), split="val", imgsz=args.imgsz, batch=args.batch,
                                       project=str(HERE / "runs"), name="val_live", exist_ok=True, plots=True)
    names = metrics.names
    print("\nVAL SPLIT (same session, later in time)   precision  recall  mAP50  mAP50-95")
    for i, c in enumerate(metrics.box.ap_class_index):
        p, r, ap50, ap = metrics.box.class_result(i)
        print(f"  {names[int(c)]:<14} {p:9.3f} {r:7.3f} {ap50:6.3f} {ap:8.3f}")
    print(f"  {'all':<14} {metrics.box.mp:9.3f} {metrics.box.mr:7.3f} {metrics.box.map50:6.3f} {metrics.box.map:8.3f}")


if __name__ == "__main__":
    main()
