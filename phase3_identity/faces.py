"""Face detection + embedding via InsightFace (SCRFD detector + ArcFace recogniser),
plus the quality gate from the MVP Acceptance Criteria (S5.4).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from .config import Phase3Config

_DLLS_READY = False


def _enable_onnx_cuda_dlls() -> None:
    """Make onnxruntime-gpu find CUDA/cuDNN on Windows.

    ORT doesn't auto-discover the DLLs from the `nvidia-*-cu12` wheels or from
    torch's bundle, so it silently falls back to CPU. Import torch first (loads
    its CUDA runtime into the process), add the nvidia wheel bin dirs to the DLL
    search path, then call ORT's preload helper.
    """
    global _DLLS_READY
    if _DLLS_READY:
        return
    _DLLS_READY = True
    import os
    import sys

    try:
        import torch  # noqa: F401  - loads cudart/cublas/cudnn into the process
    except Exception:
        pass

    site = os.path.join(sys.prefix, "Lib", "site-packages")
    for pkg in ("cudnn", "cublas", "cuda_runtime", "cufft", "cuda_nvrtc"):
        for leaf in ("bin", os.path.join("lib", "x64")):
            d = os.path.join(site, "nvidia", pkg, leaf)
            if os.path.isdir(d):
                try:
                    os.add_dll_directory(d)
                except OSError:
                    pass
    torch_lib = os.path.join(site, "torch", "lib")
    if os.path.isdir(torch_lib):
        try:
            os.add_dll_directory(torch_lib)
        except OSError:
            pass

    try:
        import onnxruntime as ort
        if hasattr(ort, "preload_dlls"):
            ort.preload_dlls()
    except Exception:
        pass


@dataclass
class DetectedFace:
    x1: int
    y1: int
    x2: int
    y2: int
    det_score: float
    kps: np.ndarray          # (5, 2) - left eye, right eye, nose, left mouth, right mouth
    embedding: np.ndarray    # (512,) L2-normalised
    iod: float
    yaw: float               # estimated, degrees (0 = frontal)
    blur: float              # variance of Laplacian on the aligned crop

    @property
    def cx(self) -> int:
        return (self.x1 + self.x2) // 2

    @property
    def cy(self) -> int:
        return (self.y1 + self.y2) // 2


class QualityResult:
    __slots__ = ("passed", "reasons")

    def __init__(self, passed: bool, reasons: list[str]) -> None:
        self.passed = passed
        self.reasons = reasons

    def __bool__(self) -> bool:
        return self.passed


class FaceEngine:
    def __init__(self, cfg: Phase3Config, logger) -> None:
        self.cfg = cfg
        self.log = logger

        _enable_onnx_cuda_dlls()
        from insightface.app import FaceAnalysis
        import onnxruntime as ort

        want_cuda = cfg.face_device != "cpu" and "CUDAExecutionProvider" in ort.get_available_providers()
        providers = (["CUDAExecutionProvider", "CPUExecutionProvider"] if want_cuda
                     else ["CPUExecutionProvider"])
        ctx_id = 0 if want_cuda else -1

        self.log.info("Loading InsightFace pack '%s' (%s) ...",
                      cfg.face_model_pack, "GPU" if want_cuda else "CPU")
        self.app = FaceAnalysis(
            name=cfg.face_model_pack,
            allowed_modules=["detection", "recognition"],  # skip landmarks / genderage
            providers=providers,
        )
        self.app.prepare(ctx_id=ctx_id, det_size=(cfg.face_det_size, cfg.face_det_size))
        eps = {m: s.session.get_providers()[0] for m, s in self.app.models.items()}
        self.log.info("Face engine ready (det_size=%d, %s).", cfg.face_det_size, eps)
        if want_cuda and any("CUDA" not in ep for ep in eps.values()):
            self.log.warning("Some face models fell back to CPU - recognition will be slow.")

    # ------------------------------------------------------------------ detect
    def detect(self, frame: np.ndarray) -> list[DetectedFace]:
        out: list[DetectedFace] = []
        for f in self.app.get(frame):
            x1, y1, x2, y2 = (int(v) for v in f.bbox)
            kps = np.asarray(f.kps, dtype=np.float32)
            emb = np.asarray(f.normed_embedding, dtype=np.float32)
            iod = float(np.linalg.norm(kps[0] - kps[1]))
            out.append(
                DetectedFace(
                    x1=x1, y1=y1, x2=x2, y2=y2,
                    det_score=float(f.det_score),
                    kps=kps,
                    embedding=emb,
                    iod=iod,
                    yaw=_estimate_yaw(kps, iod),
                    blur=_blur_of(frame, x1, y1, x2, y2),
                )
            )
        return out

    # ---------------------------------------------------------------- quality
    def quality(self, face: DetectedFace) -> QualityResult:
        c = self.cfg
        reasons: list[str] = []
        if face.det_score < c.min_det_score:
            reasons.append(f"det_score {face.det_score:.2f}<{c.min_det_score}")
        if face.iod < c.min_iod:
            reasons.append(f"iod {face.iod:.0f}<{c.min_iod:.0f}px")
        if abs(face.yaw) > c.max_yaw:
            reasons.append(f"yaw {face.yaw:.0f}>{c.max_yaw:.0f}deg")
        if face.blur < c.min_blur:
            reasons.append(f"blur {face.blur:.0f}<{c.min_blur:.0f}")
        return QualityResult(not reasons, reasons)


def _estimate_yaw(kps: np.ndarray, iod: float) -> float:
    """Rough yaw from 5-point keypoints: horizontal offset of the nose from the
    eye midpoint, normalised by inter-ocular distance. Good enough for a gate."""
    if iod <= 1:
        return 90.0
    eye_mid_x = (kps[0][0] + kps[1][0]) / 2.0
    nose_x = kps[2][0]
    ratio = (nose_x - eye_mid_x) / iod
    return math.degrees(math.atan(ratio * 2.0))


def _blur_of(frame: np.ndarray, x1: int, y1: int, x2: int, y2: int) -> float:
    h, w = frame.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 - x1 < 8 or y2 - y1 < 8:
        return 0.0
    crop = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(crop, cv2.CV_64F).var())


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b))  # both are L2-normalised
