"""PPE / safety compliance monitor.

Wired into `VisionPipeline.image_hooks` by phase5_dashboard/server.py. It reuses
the pipeline's person tracker (so every violation has a track id, an identity
when face recognition knows the person, and a zone) and adds one model of its
own - the PPE detector trained by phase14_ppe/train.py.

Only people standing in a zone with `ppe_required` set are checked. The model
runs on a worker thread about once a second, so the live video never waits on
it; `observe()` itself only copies a frame when a check is due.
"""
from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

import cv2
import numpy as np

from phase4_events.store import EventStore
from phase7_zones.geometry import ground_point, inflate_polygon, point_in_polygon

from .compliance import CLASS_MAP, Detection, ViolationTracker, assess_person, failing_items
from .config import PPEConfig, parse_items

_REQUIRED_CLASSES = {"helmet", "vest", "no_helmet"}
_ITEM_LABEL = {"helmet": "helmet", "vest": "vest", "gloves": "gloves", "boots": "boots", "goggles": "goggles"}


class PPEMonitor:
    def __init__(self, cfg: PPEConfig, store: EventStore, snapshot_dir: str | Path, logger) -> None:
        self.cfg = cfg
        self.store = store
        self.snapshot_dir = Path(snapshot_dir)
        self.log = logger
        self.enabled = False
        self.disabled_reason: str | None = "not started"
        self._model = None
        self._names: dict[int, str] = {}
        self._device = None
        self._tracker = ViolationTracker(cfg.confirm_checks, cfg.cooldown_s)
        self._cv = threading.Condition()
        self._job: dict | None = None
        self._busy = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_submit = 0.0
        self._zones: list[tuple] = []
        self._zones_at = 0.0
        self.checks_run = 0
        self.violations_logged = 0
        self.infer_ms = 0.0
        self.live: dict = {"ts": None, "people": []}

    # --------------------------------------------------------------- control
    def start(self) -> None:
        path = Path(self.cfg.model)
        if not path.is_file():
            self.disabled_reason = f"No PPE model at {path}. Train one with: python -m phase14_ppe.train"
            self.log.warning("PPE monitoring disabled - %s", self.disabled_reason)
            return
        import torch
        from ultralytics import YOLO

        model = YOLO(str(path))
        names = {int(k): v for k, v in model.names.items()}
        if not _REQUIRED_CLASSES <= set(names.values()):
            self.disabled_reason = f"{path.name} isn't a PPE model (classes: {', '.join(names.values())})"
            self.log.warning("PPE monitoring disabled - %s", self.disabled_reason)
            return
        self._device = self.cfg.device or (0 if torch.cuda.is_available() else "cpu")
        model.predict(np.zeros((self.cfg.imgsz, self.cfg.imgsz, 3), np.uint8),
                      imgsz=self.cfg.imgsz, device=self._device, verbose=False)  # warm-up
        self._model, self._names = model, names
        self._thread = threading.Thread(target=self._run, name="ppe", daemon=True)
        self._thread.start()
        self.enabled, self.disabled_reason = True, None
        self.log.info("PPE monitoring ready. model=%s device=%s every %.1fs, confirm after %d checks",
                      path.name, self._device, self.cfg.check_interval_s, self.cfg.confirm_checks)

    def stop(self) -> None:
        self._stop.set()
        with self._cv:
            self._cv.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=3.0)

    def status(self) -> dict:
        return {
            "enabled": self.enabled, "disabled_reason": self.disabled_reason,
            "model": Path(self.cfg.model).name, "check_interval_s": self.cfg.check_interval_s,
            "confirm_checks": self.cfg.confirm_checks, "checks_run": self.checks_run,
            "violations_logged": self.violations_logged, "infer_ms": round(self.infer_ms),
            "default_items": self.cfg.default_items, "live": self.live,
        }

    # ---------------------------------------------------- pipeline thread
    def _ppe_zones(self, camera_id: str, now: float) -> list[tuple]:
        if now - self._zones_at > 5.0:  # zones change rarely; don't hit SQLite every frame
            self._zones_at = now
            self._zones = [
                (z, inflate_polygon(z.points, self.cfg.zone_margin), parse_items(z.ppe_required))
                for z in self.store.list_zones(enabled_only=True) if parse_items(z.ppe_required)
            ]
        return [t for t in self._zones if t[0].camera_id == camera_id]

    def observe(self, camera_id: str, frame, positions: dict, tracks: dict) -> None:
        if not self.enabled:
            return
        now = time.time()
        if self._busy or now - self._last_submit < self.cfg.check_interval_s:
            return
        zones = self._ppe_zones(camera_id, now)
        if not zones:
            self.live = {"ts": now, "people": []}
            return
        fh, fw = frame.shape[:2]
        people = []
        for tid, bbox in positions.items():
            pt = ground_point(bbox, fw, fh)
            hit = next(((z, items) for z, poly, items in zones if point_in_polygon(pt, poly)), None)
            if hit is None:
                continue
            zone, items = hit
            ti = tracks.get(tid)
            people.append({
                "track_id": tid, "bbox": tuple(float(v) for v in bbox), "zone_id": zone.id,
                "zone_name": zone.name, "required": items,
                "identity_status": ti.state.value if ti else "UNVERIFIED",
                "employee_code": ti.employee_code if ti else None,
                "employee_name": ti.employee_name if ti else None,
            })
        self._last_submit = now
        if not people:
            self.live = {"ts": now, "people": []}
            return
        with self._cv:
            self._job = {"ts": now, "camera_id": camera_id, "frame": frame.copy(), "people": people,
                         "all_boxes": [tuple(float(v) for v in b) for b in positions.values()]}
            self._busy = True
            self._cv.notify()

    # ------------------------------------------------------- worker thread
    def _run(self) -> None:
        while not self._stop.is_set():
            with self._cv:
                while self._job is None and not self._stop.is_set():
                    self._cv.wait(timeout=1.0)
                job, self._job = self._job, None
            if job is None:
                continue
            try:
                self._check(job)
            except Exception:  # noqa: BLE001 - one bad check must not kill monitoring
                self.log.exception("PPE check failed")
            finally:
                self._busy = False

    def detect(self, frame) -> list[Detection]:
        """Run the PPE model on one frame (also used by the offline evaluation)."""
        r = self._model.predict(frame, imgsz=self.cfg.imgsz, conf=self.cfg.conf,
                                device=self._device, verbose=False)[0]
        return [Detection(self._names[int(c)], float(p), tuple(float(v) for v in b))
                for b, c, p in zip(r.boxes.xyxy.tolist(), r.boxes.cls.tolist(), r.boxes.conf.tolist())]

    def _check(self, job: dict) -> None:
        t0 = time.perf_counter()
        dets = self.detect(job["frame"])
        self.infer_ms = (time.perf_counter() - t0) * 1000
        fh = job["frame"].shape[0]
        live = []
        for p in job["people"]:
            others = [b for b in job["all_boxes"] if b != p["bbox"]]  # everyone in frame, zone or not
            status = assess_person(p["bbox"], dets, p["required"], fh, self.cfg.min_person_px, others)
            confirmed = self._tracker.update(p["track_id"], status, job["ts"])
            fail, _ = failing_items(status)
            live.append({
                **{k: p[k] for k in ("track_id", "zone_name", "identity_status", "employee_code", "employee_name")},
                "items": status, "failing": fail,
                "streak": {i: self._tracker.streak(p["track_id"], i) for i in fail},
            })
            if confirmed:
                self._log_violation(job, p, confirmed, dets)
        self._tracker.prune(job["ts"])
        self.checks_run += 1
        self.live = {"ts": job["ts"], "people": live}

    def _log_violation(self, job: dict, person: dict, missing: list[str], dets: list[Detection]) -> None:
        uid = uuid.uuid4().hex
        try:
            snap = self._save_snapshot(uid, job, person, missing, dets)
        except Exception as e:  # noqa: BLE001 - evidence is best-effort, the record isn't
            self.log.warning("PPE snapshot %s failed: %s", uid, e)
            snap = None
        v = self.store.create_ppe_violation(
            violation_uid=uid, ts=job["ts"], camera_id=job["camera_id"], track_id=person["track_id"],
            zone_id=person["zone_id"], zone_name=person["zone_name"], missing=missing,
            identity_status=person["identity_status"], employee_code=person["employee_code"],
            employee_name=person["employee_name"], snapshot=snap,
        )
        self.violations_logged += 1
        who = person["employee_name"] or f"track {person['track_id']} ({person['identity_status']})"
        self.log.warning("PPE VIOLATION #%d %s missing %s in '%s'", v.id, who, ", ".join(missing),
                         person["zone_name"])

    def _save_snapshot(self, uid: str, job: dict, person: dict, missing: list[str],
                       dets: list[Detection]) -> str:
        img = job["frame"].copy()
        h, w = img.shape[:2]
        x1, y1, x2, y2 = (int(v) for v in person["bbox"])
        for d in dets:  # the PPE evidence around this person: green = worn, red = explicit "no_*"
            dx1, dy1, dx2, dy2 = (int(v) for v in d.box)
            if dx2 < x1 or dx1 > x2 or dy2 < y1 or dy1 > y2:
                continue
            item, negative = CLASS_MAP.get(d.cls, (d.cls, False))  # "none" is the no-vest class
            colour = (0, 0, 255) if negative else (0, 200, 0)
            label = f"no_{item}" if negative else d.cls
            cv2.rectangle(img, (dx1, dy1), (dx2, dy2), colour, 2)
            cv2.putText(img, label, (dx1, max(dy1 - 4, 12)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, colour, 1, cv2.LINE_AA)
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 255), 3)
        tag = "NO " + " / NO ".join(_ITEM_LABEL[m].upper() for m in missing)
        (tw, th), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        ty = max(y1 - 8, th + 6)
        cv2.rectangle(img, (x1, ty - th - 6), (x1 + tw + 8, ty + 4), (0, 0, 255), -1)
        cv2.putText(img, tag, (x1 + 4, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(job["ts"]))
        who = person["employee_name"] or person["identity_status"]
        label = f"PPE  {who}  track {person['track_id']}  zone {person['zone_name']}  {stamp}  cam {job['camera_id']}"
        cv2.rectangle(img, (0, h - 26), (w, h), (0, 0, 0), -1)
        cv2.putText(img, label, (8, h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        day_dir = self.snapshot_dir / time.strftime("%Y-%m-%d", time.localtime(job["ts"]))
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / f"ppe_{uid}.jpg"
        cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return str(path.relative_to(self.snapshot_dir)).replace("\\", "/")
