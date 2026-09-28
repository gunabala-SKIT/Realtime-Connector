"""Score the full PPE decision - detector + the attribution/violation rules in
compliance.py - per person, on the held-out test split.

    python -m phase14_ppe.evaluate

A detector's mAP says how well it boxes helmets; this says how often the thing
that raises an alarm ("this person has no helmet") is right. Ground truth per
labelled Person box comes from the human labels around it, using the same
head/torso/hand regions the live monitor uses:

  helmet: 'helmet' label -> worn, 'no_helmet' label -> missing, neither -> not judged
  gloves: 'gloves' -> worn, 'no_gloves' -> missing, neither -> not judged
  vest:   'vest' -> worn, 'none' (the dataset's bare-torso label) -> missing,
          neither -> not judged. A missing vest *label* is not evidence: several
          test images show clearly worn hi-vis vests with no vest box at all.

Person boxes are the labelled ones, so this isolates PPE judgement from the
person tracker (which is COCO YOLO, measured elsewhere).
"""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2

from .compliance import CLASS_MAP, Detection, _in_region, assess_person, failing_items
from .config import PPEConfig

HERE = Path(__file__).resolve().parent
DATA = HERE / "datasets" / "construction-ppe"
NAMES = ["helmet", "gloves", "vest", "boots", "goggles", "none", "Person",
         "no_helmet", "no_goggle", "no_gloves", "no_boots"]
ITEMS = ["helmet", "vest", "gloves"]


def _labels(path: Path, w: int, h: int) -> list[Detection]:
    out = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        c, cx, cy, bw, bh = line.split()[:5]
        cx, cy, bw, bh = float(cx) * w, float(cy) * h, float(bw) * w, float(bh) * h
        out.append(Detection(NAMES[int(c)], 1.0, (cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2)))
    return out


def _truth(person, labels: list[Detection]) -> dict[str, str | None]:
    """Human-label verdict per item, using the same head/torso/hand regions as the live monitor."""
    out: dict[str, str | None] = {}
    for item in ITEMS:
        pos = any(CLASS_MAP.get(d.cls) == (item, False) and _in_region(d, person, item) for d in labels)
        neg = any(CLASS_MAP.get(d.cls) == (item, True) and _in_region(d, person, item) for d in labels)
        out[item] = "worn" if pos else "missing" if neg else None
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="test")
    ap.add_argument("--save-examples", type=int, default=0, help="write N annotated mistakes to runs/eval/")
    args = ap.parse_args()

    from ultralytics import YOLO

    cfg = PPEConfig.load()
    model = YOLO(cfg.model)
    names = {int(k): v for k, v in model.names.items()}
    stats = {i: {"tp": 0, "fp": 0, "fn": 0, "tn": 0, "not_judged": 0} for i in ITEMS}
    people = judged_people = 0
    mistakes = []
    for img_path in sorted((DATA / "images" / args.split).iterdir()):
        img = cv2.imread(str(img_path))
        if img is None:
            continue
        h, w = img.shape[:2]
        labels = _labels(DATA / "labels" / args.split / (img_path.stem + ".txt"), w, h)
        r = model.predict(img, imgsz=cfg.imgsz, conf=cfg.conf, verbose=False)[0]
        dets = [Detection(names[int(c)], float(p), tuple(b))
                for b, c, p in zip(r.boxes.xyxy.tolist(), r.boxes.cls.tolist(), r.boxes.conf.tolist())]
        persons = [d.box for d in labels if d.cls == "Person"]
        for person in persons:
            people += 1
            truth = _truth(person, labels)
            others = [p for p in persons if p is not person]
            status = assess_person(person, dets, ITEMS, h, cfg.min_person_px, others)
            fail, _ = failing_items(status)
            judged_any = False
            for item in ITEMS:
                if truth[item] is None or status[item] == "unknown":
                    stats[item]["not_judged"] += 1
                    continue
                judged_any = True
                actual, predicted = truth[item] == "missing", item in fail
                key = {(True, True): "tp", (False, True): "fp", (True, False): "fn", (False, False): "tn"}
                stats[item][key[(actual, predicted)]] += 1
                if actual != predicted:
                    mistakes.append((img_path, person, item, actual, dets))
            judged_people += judged_any

    print(f"\n{args.split} split: {people} labelled people, {judged_people} large enough to judge\n")
    print("item     violations  caught  false alarms  missed   precision  recall   accuracy")
    for item in ITEMS:
        s = stats[item]
        n = s["tp"] + s["fp"] + s["fn"] + s["tn"]
        prec = s["tp"] / (s["tp"] + s["fp"]) if s["tp"] + s["fp"] else float("nan")
        rec = s["tp"] / (s["tp"] + s["fn"]) if s["tp"] + s["fn"] else float("nan")
        acc = (s["tp"] + s["tn"]) / n if n else float("nan")
        print(f"{item:<8} {s['tp'] + s['fn']:>10} {s['tp']:>7} {s['fp']:>13} {s['fn']:>7}   "
              f"{prec:9.1%} {rec:7.1%} {acc:9.1%}   ({n} judged, {s['not_judged']} not judged)")

    if args.save_examples:
        out = HERE / "runs" / "eval"
        out.mkdir(parents=True, exist_ok=True)
        for i, (path, person, item, actual, dets) in enumerate(mistakes[:args.save_examples]):
            img = cv2.imread(str(path))
            x1, y1, x2, y2 = (int(v) for v in person)
            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 255), 2)
            for d in dets:
                bx1, by1, bx2, by2 = (int(v) for v in d.box)
                cv2.rectangle(img, (bx1, by1), (bx2, by2), (0, 200, 0), 1)
                cv2.putText(img, f"{d.cls} {d.conf:.2f}", (bx1, max(by1 - 3, 10)), cv2.FONT_HERSHEY_SIMPLEX,
                            0.4, (0, 200, 0), 1)
            kind = "missed" if actual else "false_alarm"
            cv2.imwrite(str(out / f"{i:02d}_{kind}_{item}_{path.stem}.jpg"), img)
        print(f"\nwrote {min(len(mistakes), args.save_examples)} annotated mistakes to {out}")


if __name__ == "__main__":
    main()
