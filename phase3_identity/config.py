"""Phase 3 configuration - reuses Phase 1 (camera) + Phase 2 (detector) settings
and adds the face / recognition / identity-state parameters.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from phase2_detect.config import Phase2Config

BASE_DIR = Path(__file__).resolve().parent


def _get(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    return v if v not in (None, "") else default


def _get_int(name: str, default: int) -> int:
    try:
        return int(_get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _get_float(name: str, default: float) -> float:
    try:
        return float(_get(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass
class Phase3Config:
    phase2: Phase2Config

    # face detection / analysis (InsightFace)
    face_model_pack: str          # buffalo_l | buffalo_s
    face_det_size: int
    face_device: str              # "", "cpu", "cuda"

    # quality gate (acceptance doc S5.4)
    min_iod: float                # inter-ocular distance, px
    max_yaw: float                # degrees
    min_blur: float               # variance of Laplacian
    min_det_score: float

    # recognition
    gallery_db: Path
    recognition_threshold: float  # cosine similarity operating point (fixed, documented)
    recognize_every_n: int        # per-track recognition cadence (perf)
    identity_confirm_frames: int  # multi-frame confirmation before RECOGNIZED / UNKNOWN
    identity_flip_cooldown_s: float

    log_level: str
    log_dir: Path

    @classmethod
    def load(cls, env_file: str | os.PathLike | None = None) -> "Phase3Config":
        phase2 = Phase2Config.load(env_file)  # runs load_dotenv()

        gallery_db = Path(_get("GALLERY_DB", str(BASE_DIR / "gallery.db")))
        if not gallery_db.is_absolute():
            gallery_db = BASE_DIR / gallery_db

        log_dir = Path(_get("LOG_DIR", "logs"))
        if not log_dir.is_absolute():
            log_dir = BASE_DIR / log_dir

        pack = _get("FACE_MODEL_PACK", "buffalo_l") or "buffalo_l"

        return cls(
            phase2=phase2,
            face_model_pack=pack,
            face_det_size=_get_int("FACE_DET_SIZE", 640),
            face_device=_get("FACE_DEVICE", "") or "",
            min_iod=_get_float("FACE_MIN_IOD", 40.0),
            max_yaw=_get_float("FACE_MAX_YAW", 30.0),
            min_blur=_get_float("FACE_BLUR_MIN", 55.0),
            min_det_score=_get_float("FACE_DET_SCORE_MIN", 0.55),
            gallery_db=gallery_db,
            recognition_threshold=_get_float("RECOGNITION_THRESHOLD", 0.35),
            recognize_every_n=max(1, _get_int("RECOGNIZE_EVERY_N", 5)),
            identity_confirm_frames=max(1, _get_int("IDENTITY_CONFIRM_FRAMES", 3)),
            identity_flip_cooldown_s=_get_float("IDENTITY_FLIP_COOLDOWN_S", 2.0),
            log_level=(_get("LOG_LEVEL", "INFO") or "INFO").upper(),
            log_dir=log_dir,
        )
