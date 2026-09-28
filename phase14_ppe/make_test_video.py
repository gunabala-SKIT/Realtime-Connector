"""Build a test/demo clip from held-out Construction-PPE test images whose
labels give a clear verdict, alternating compliant and non-compliant scenes.

    python -m phase14_ppe.make_test_video            # -> phase14_ppe/test_video.mp4 + .json

Point the camera at it (CAMERA_SOURCE=phase14_ppe/test_video.mp4), mark a
full-frame zone as a PPE zone, and each non-compliant scene should produce a
violation while compliant scenes produce none. The .json lists each scene's
time range and what its labels say, to check the result against.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from .evaluate import DATA, _labels, _truth

HERE = Path(__file__).resolve().parent
W, H, FPS = 1280, 720, 10


def _letterbox(img: np.ndarray) -> tuple[np.ndarray, float]:
    scale = min(W / img.shape[1], H / img.shape[0])
    resized = cv2.resize(img, (int(img.shape[1] * scale), int(img.shape[0] * scale)))
    canvas = np.zeros((H, W, 3), np.uint8)
    y, x = (H - resized.shape[0]) // 2, (W - resized.shape[1]) // 2
    canvas[y:y + resized.shape[0], x:x + resized.shape[1]] = resized
    return canvas, scale


def pick_scenes(n_each: int) -> tuple[list, list]:
    compliant, violating = [], []
    for img_path in sorted((DATA / "images" / "test").iterdir()):
        img = cv2.imread(str(img_path))
        if img is None:
            continue
        h, w = img.shape[:2]
        labels = _labels(DATA / "labels" / "test" / (img_path.stem + ".txt"), w, h)
        people = [d.box for d in labels if d.cls == "Person"]
        scale = min(W / w, H / h)
        # 1-2 people, each tall on screen, so the scene is unambiguous at a glance
        if not 1 <= len(people) <= 2 or any((b[3] - b[1]) * scale < 0.30 * H for b in people):
            continue
        truths = [_truth(b, labels) for b in people]
        if any(t[i] is None for t in truths for i in ("helmet", "vest")):
            continue  # labels must state both items explicitly for every person
        missing = sorted({i for t in truths for i in ("helmet", "vest") if t[i] == "missing"})
        scene = {"image": img_path.name, "people": len(people), "missing": missing}
        (violating if missing else compliant).append(scene)
    # rotate through the kinds of violation (helmet only / vest only / both) so the
    # clip shows the items are told apart, not just "no PPE at all"
    by_kind: dict[tuple, list] = {}
    for s in violating:
        by_kind.setdefault(tuple(s["missing"]), []).append(s)
    mixed = [s for group in zip(*by_kind.values()) for s in group] if by_kind else []
    mixed += [s for s in violating if s not in mixed]
    return compliant[:n_each], mixed[:n_each]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenes", type=int, default=4, help="compliant and non-compliant scenes each")
    ap.add_argument("--seconds", type=float, default=8.0, help="how long each scene is held")
    ap.add_argument("--out", default=str(HERE / "test_video.mp4"))
    args = ap.parse_args()

    compliant, violating = pick_scenes(args.scenes)
    order = [s for pair in zip(compliant, violating) for s in pair]
    out = Path(args.out)
    writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    manifest, t = [], 0.0
    for i, scene in enumerate(order):
        frame, _ = _letterbox(cv2.imread(str(DATA / "images" / "test" / scene["image"])))
        for _ in range(int(args.seconds * FPS)):
            writer.write(frame)
        manifest.append({**scene, "scene": i + 1, "start_s": t, "end_s": t + args.seconds,
                         "expect": f"violation: no {', no '.join(scene['missing'])}" if scene["missing"] else "compliant"})
        t += args.seconds
    writer.release()
    Path(out.with_suffix(".json")).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    for s in manifest:
        print(f"scene {s['scene']}  {s['start_s']:5.0f}-{s['end_s']:<4.0f}s  {s['image']:<16} "
              f"{s['people']} person(s)  expect {s['expect']}")
    print(f"\n{out}  ({t:.0f}s, {W}x{H} @ {FPS} fps)")


if __name__ == "__main__":
    main()
