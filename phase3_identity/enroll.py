"""Employee face enrolment (BRD S6, S19).

    python -m phase3_identity.enroll --code EMP001 --name "Asha Rao" \
        --dept Engineering images/asha/*.jpg

For each image: detect exactly one good-quality face, embed it, average the
embeddings across images, store in the gallery. Bad images are rejected with a
reason (no face / multiple faces / too small / blurred / extreme pose).
"""
from __future__ import annotations

import argparse
import glob
import logging
import sys

import cv2
import numpy as np

from .config import Phase3Config
from .faces import FaceEngine


def _expand(patterns: list[str]) -> list[str]:
    files: list[str] = []
    for p in patterns:
        hits = glob.glob(p)
        files.extend(hits or [p])
    return sorted(dict.fromkeys(files))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Reality Connector - employee face enrolment")
    ap.add_argument("--code", required=True, help="employee code (unique)")
    ap.add_argument("--name", required=True, help="employee full name")
    ap.add_argument("--dept", default=None, help="department (optional)")
    ap.add_argument("images", nargs="+", help="image files / globs (>= 1, ideally 3+)")
    ap.add_argument("--min-images", type=int, default=1, help="fail if fewer usable images than this")
    ap.add_argument("--env", help="alternate .env file")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log = logging.getLogger("enroll")

    cfg = Phase3Config.load(args.env)
    engine = FaceEngine(cfg, log)

    from .gallery import EmployeeGallery
    gallery = EmployeeGallery(cfg.gallery_db)

    files = _expand(args.images)
    if not files:
        log.error("No image files matched: %s", args.images)
        return 2

    embeddings: list[np.ndarray] = []
    for path in files:
        img = cv2.imread(path)
        if img is None:
            log.warning("%s : cannot read - skipped", path)
            continue
        faces = engine.detect(img)
        if len(faces) == 0:
            log.warning("%s : no face detected - skipped", path)
            continue
        if len(faces) > 1:
            log.warning("%s : %d faces (expected 1) - skipped", path, len(faces))
            continue
        face = faces[0]
        q = engine.quality(face, for_enroll=True)
        if not q.passed:
            log.warning("%s : rejected - %s", path, "; ".join(q.reasons))
            continue
        embeddings.append(face.embedding)
        log.info("%s : OK  (eyes %.0fpx apart, yaw %+.0f, sharpness %.0f, det %.2f)",
                 path, face.iod, face.yaw, face.blur, face.det_score)

    if len(embeddings) < args.min_images:
        log.error("Only %d usable image(s), need >= %d. Nothing enrolled.",
                  len(embeddings), args.min_images)
        log.error("Enrolment photos should be close-up headshots: face fills at least a third "
                  "of the frame, eyes level, looking at the camera, well lit, in focus. "
                  "A phone selfie at arm's length works well.")
        gallery.close()
        return 1

    mean = np.mean(np.stack(embeddings), axis=0)
    mean = mean / (np.linalg.norm(mean) + 1e-9)

    # coherence check: are the enrolment images actually the same person?
    sims = [float(np.dot(e, mean)) for e in embeddings]
    if len(sims) > 1 and min(sims) < 0.5:
        log.warning("enrolment images look inconsistent (min sim to mean %.2f) - "
                    "check they are all the same person", min(sims))

    emp_id = gallery.upsert(args.code, args.name, args.dept, mean, len(embeddings))
    log.info("Enrolled '%s' (%s) id=%d from %d image(s). Gallery now has %d employee(s).",
             args.name, args.code, emp_id, len(embeddings), len(gallery))
    gallery.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
