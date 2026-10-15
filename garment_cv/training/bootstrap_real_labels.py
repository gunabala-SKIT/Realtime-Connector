"""Draft labels for the frames captured by `capture_real_footage.py` (real footage from the real
camera, unlike the AI-generated set in `prepare_real_dataset.py`).

These are DRAFTS, found automatically and meant to be checked by eye before training, not trusted
blindly:
  - person           -> the COCO-pretrained model (a bootstrap, per the user's "semi-automated" choice)
  - scissors, pen    -> colour (the scissors are saturated orange, the pen is pink/magenta)
  - cloth/fabric     -> "anything on the blue table that isn't table blue", with the camera's
                        fixed junk (table edges, shadow) filtered out. Only reliable when nobody
                        is touching the cloth - action frames need a manual look.

The camera and table never move between captures, which is what makes colour + a fixed region of
interest dependable here. If the camera is moved, `ROI` and `JUNK` below need redoing.

    python -m garment_cv.training.bootstrap_real_labels
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
RAW = HERE / "datasets" / "real" / "raw"
W, H = 2560, 1440

# Table-top region in the camera's native 2560x1440 frame, and components that show up in the
# same place in every static capture (table edge, front shadow) - found by comparing the three
# "object alone" folders, not guessed.
ROI = np.array([(698, 1011), (1933, 1146), (1933, 1440), (384, 1440), (335, 1235)], np.int32)
JUNK = [(336, 1229, 1174, 1440), (1072, 1052, 1691, 1124), (1856, 1283, 1934, 1333)]


def _iou(a, b) -> float:
    ix1, iy1, ix2, iy2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    if ix1 >= ix2 or iy1 >= iy2:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union


def _components(mask, min_area: int):
    n, _lab, stats, _ = cv2.connectedComponentsWithStats(mask)
    out = [(int(x), int(y), int(x + w), int(y + h), int(a)) for x, y, w, h, a in stats[1:] if a >= min_area]
    return sorted(out, key=lambda b: -b[4])


def table_objects(img) -> list[tuple[int, int, int, int, int]]:
    """Non-table-coloured things lying on the table top, biggest first, junk removed."""
    roi = np.zeros((H, W), np.uint8)
    cv2.fillPoly(roi, [ROI], 255)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    table = cv2.inRange(hsv, (96, 60, 130), (116, 170, 255))
    obj = cv2.bitwise_and(cv2.bitwise_not(table), roi)
    obj = cv2.morphologyEx(obj, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    obj = cv2.morphologyEx(obj, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    return [b for b in _components(obj, 400) if not any(_iou(b[:4], j) > 0.8 for j in JUNK)]


def _colour_boxes(img, ranges, min_area: int, max_side: int) -> list[tuple[int, int, int, int, int]]:
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = np.zeros(hsv.shape[:2], np.uint8)
    for lo, hi in ranges:
        mask |= cv2.inRange(hsv, lo, hi)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.dilate(mask, np.ones((25, 25), np.uint8))  # join the two orange handles / blade bits
    return [b for b in _components(mask, min_area) if max(b[2] - b[0], b[3] - b[1]) <= max_side]


def scissors_boxes(img):
    return _colour_boxes(img, [((0, 150, 190), (12, 255, 255)), ((172, 150, 190), (180, 255, 255))], 800, 420)


def pen_boxes(img):
    """Pink pen. Only searched for over the table area: the colour match also fires on a pink
    scarf, chair and bag belonging to someone sitting further back, which are not pens."""
    boxes = _colour_boxes(img, [((140, 110, 120), (172, 255, 255))], 500, 300)
    return [b for b in boxes if b[0] > 335 and b[2] < 1850 and b[1] > 850]


_yolo = None


def person_boxes(img, conf: float = 0.25):
    global _yolo
    if _yolo is None:
        from ultralytics import YOLO
        _yolo = YOLO("yolo11n.pt")
    r = _yolo.predict(img, conf=conf, classes=[0], verbose=False)[0]
    return [tuple(int(v) for v in b) + (0,) for b in r.boxes.xyxy.tolist()]


def frames(folder: str) -> list[Path]:
    return sorted((RAW / folder).glob("*.jpg"))


def draw(img, boxes_by_class: dict[str, list[tuple]], thickness: int = 5):
    colours = {"person": (0, 200, 0), "fabric": (255, 120, 0), "scissors": (0, 0, 255),
               "marking_tool": (255, 0, 255), "fabric_piece": (0, 200, 255)}
    for cls, boxes in boxes_by_class.items():
        for b in boxes:
            cv2.rectangle(img, (b[0], b[1]), (b[2], b[3]), colours.get(cls, (255, 255, 255)), thickness)
            cv2.putText(img, cls, (b[0], max(30, b[1] - 10)), cv2.FONT_HERSHEY_SIMPLEX, 1.4,
                        colours.get(cls, (255, 255, 255)), 4)
    return img


# ---------------------------------------------------------------------------------------------
# Building the training dataset from the drafts above (`python -m ...bootstrap_real_labels`)
# ---------------------------------------------------------------------------------------------
CLASSES = ("person", "fabric", "scissors", "marking_tool", "fabric_piece")

# A second person sits at a desk further back, usually facing away from the camera - too occluded
# for the COCO model to find, but a real person, and leaving her unlabeled would teach the model
# she is background. Added by hand (this fixed box) only when a pink-clothing check says she's there.
WOMAN = (1925, 440, 2365, 1145)

# One cloth box per action folder, read off the frames and checked in a montage - the cloth does not
# move while it is being marked/cut, and automatic segmentation fails whenever hands touch it.
CLOTH_FIXED = {
    "marking_action": (1052, 1126, 1546, 1316),
    "cutting_action": (813, 1073, 1645, 1274),
    "holding_scissors_not_cutting": (730, 1075, 1570, 1250),
    "scissors_pick_place": (780, 1103, 1645, 1308),
}
# fabric_piece folder: one piece lies still on the table, the other is carried around, so its box is
# placed by hand per frame (frame index -> box). Only these frames are used.
LYING_PIECE = (1144, 1121, 1567, 1298)
CARRIED_PIECE = {0: (960, 902, 1267, 1152), 3: (868, 787, 1203, 1019), 6: (783, 914, 1072, 1198),
                 9: (691, 1125, 1152, 1267), 12: (856, 768, 1144, 1019)}
STATIC = {"fabric": "fabric", "scissors": "scissors", "marking_tool": "marking_tool"}
SCISSORS_BY_COLOUR = {"cutting_action", "holding_scissors_not_cutting", "scissors_pick_place", "fabric_piece"}


def woman_present(img) -> bool:
    x1, y1, x2, y2 = WOMAN
    hsv = cv2.cvtColor(img[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
    return cv2.inRange(hsv, (150, 50, 110), (178, 200, 255)).mean() / 255 > 0.04


def arm_intrudes(img) -> bool:
    """Someone else's dark sleeve entering at the bottom-right edge - those frames are dropped
    rather than left with an unlabeled partial person in them."""
    v = cv2.cvtColor(img[1100:1440, 2250:2560], cv2.COLOR_BGR2HSV)[:, :, 2]
    return v.mean() < 85


def persons(img) -> list[tuple[int, int, int, int]]:
    boxes = [b[:4] for b in person_boxes(img, conf=0.4)]
    if boxes:
        keep = cv2.dnn.NMSBoxes([[b[0], b[1], b[2] - b[0], b[3] - b[1]] for b in boxes],
                                [1.0] * len(boxes), 0.0, 0.45)
        boxes = [boxes[int(i)] for i in np.array(keep).flatten()]
    if woman_present(img) and not any(_iou(b, WOMAN) > 0.2 for b in boxes):
        boxes.append(WOMAN)
    return boxes


def _pad(box, p: int = 20):
    return (max(0, box[0] - p), max(0, box[1] - p), min(W, box[2] + p), min(H, box[3] + p))


# Frames from a later session where the camera framing had changed (the table sits further left and
# higher in the picture). The fixed boxes above (ROI, WOMAN, CLOTH_FIXED) only fit the first framing, so
# these use boxes drafted from the trained model's own person/cloth detections plus an orange-colour
# scissors box, then checked by eye in a montage and saved as `labels_new_framing.json`. Person and cloth
# boxes being model-drafted is a real caveat: they inherit that model's habits, which the check only
# partly catches.
NEW_FRAMING_FOLDERS = ("cutting_new_framing", "scissors_on_cloth_new_framing", "scraps_orange",
                       "fabric_yellow", "scissors_yellow", "pen_yellow", "pen_checked", "cutting_closeup",
                       "marking_in_hand_1", "marking_in_hand_2", "marking_in_hand_3_yellow", "marking_then_cutting_4")
_NEW_FRAMING = None


def _new_framing_labels(folder: str, stem: str):
    global _NEW_FRAMING
    if _NEW_FRAMING is None:
        _NEW_FRAMING = json.loads((HERE / "labels_new_framing.json").read_text(encoding="utf-8"))
    d = _NEW_FRAMING.get(folder, {}).get(stem)
    if not d:
        return None
    out = {cls: [tuple(b[:4]) for b in boxes] for cls, boxes in d.items() if boxes}
    return out or None


def labels_for(folder: str, idx: int, img) -> dict[str, list[tuple[int, int, int, int]]] | None:
    """Class -> boxes for one frame, or None if the frame should be skipped."""
    if folder in STATIC:
        obj = table_objects(img)
        if not obj:
            return None
        d = {STATIC[folder]: [obj[0][:4]]}
        if woman_present(img):
            d["person"] = persons(img)
        return d
    if folder == "fabric_piece":
        if idx not in CARRIED_PIECE:
            return None
        d = {"person": persons(img), "fabric_piece": [LYING_PIECE, CARRIED_PIECE[idx]]}
        sc = scissors_boxes(img)   # scissors are put down at the table's left edge during this clip
        if sc:
            d["scissors"] = [_pad(sc[0][:4])]
        return d
    # frames 8-13 of this clip have another person's hand reaching in from the right (the brightness
    # check alone missed frame 8, whose hand is only partly in view) - dropped, not half-labeled
    if folder == "holding_scissors_not_cutting" and (arm_intrudes(img) or 8 <= idx <= 13):
        return None
    d = {"person": persons(img), "fabric": [CLOTH_FIXED[folder]]}
    if folder in SCISSORS_BY_COLOUR:
        s = scissors_boxes(img)
        if s:
            d["scissors"] = [_pad(s[0][:4])]
    return d


def build_dataset(out_dir: Path | None = None, val_fraction: float = 0.3) -> dict:
    out = out_dir or HERE / "datasets" / "live"
    for sub in ("images/train", "images/val", "labels/train", "labels/val"):
        (out / sub).mkdir(parents=True, exist_ok=True)
        for old in (out / sub).glob("*"):
            old.unlink()
    summary = {"train": 0, "val": 0, "boxes": {c: 0 for c in CLASSES}}
    for folder in ("fabric", "scissors", "marking_tool", "marking_action", "cutting_action",
                   "holding_scissors_not_cutting", "scissors_pick_place", "fabric_piece",
                   *NEW_FRAMING_FOLDERS):
        all_frames = frames(folder)
        wanted = all_frames if folder in STATIC or folder == "fabric_piece" else all_frames[::2]
        rows = []
        for p in wanted:
            idx = int(p.stem.rsplit("_", 1)[1])
            img = cv2.imread(str(p))
            d = (_new_framing_labels(folder, p.stem) if folder in NEW_FRAMING_FOLDERS
                 else labels_for(folder, idx, img))
            if d:
                rows.append((p, img, d))
        n_val = max(1, round(len(rows) * val_fraction)) if rows else 0
        for k, (p, img, d) in enumerate(rows):
            split = "val" if k >= len(rows) - n_val else "train"   # last frames in time -> val
            small = cv2.resize(img, (1280, 720), interpolation=cv2.INTER_AREA)
            cv2.imwrite(str(out / "images" / split / f"{p.stem}.jpg"), small, [cv2.IMWRITE_JPEG_QUALITY, 92])
            lines = []
            for cls, boxes in d.items():
                for x1, y1, x2, y2 in boxes:
                    lines.append(f"{CLASSES.index(cls)} {(x1 + x2) / 2 / W:.6f} {(y1 + y2) / 2 / H:.6f} "
                                 f"{(x2 - x1) / W:.6f} {(y2 - y1) / H:.6f}")
                    summary["boxes"][cls] += 1
            (out / "labels" / split / f"{p.stem}.txt").write_text("\n".join(lines), encoding="utf-8")
            summary[split] += 1
    yaml_path = out.parent / "live.yaml"
    names = "\n".join(f"  {i}: {n}" for i, n in enumerate(CLASSES))
    yaml_path.write_text(f"path: {out.resolve().as_posix()}\ntrain: images/train\nval: images/val\n"
                         f"names:\n{names}\n", encoding="utf-8")
    return summary


if __name__ == "__main__":
    print(build_dataset())
