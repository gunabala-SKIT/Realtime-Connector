"""Queue / crowd density monitoring - counts how many currently-tracked
people fall inside each capacity-configured zone, on its own schedule
(independent of identity events), and raises a cooldown-limited alert when a
zone goes over capacity. Wired into `VisionPipeline.frame_hooks` by
`phase5_dashboard/server.py`, so it runs off the person-tracking the main
pipeline is already doing every frame - no extra camera or model needed.
"""
from __future__ import annotations

import time

from phase4_events.store import EventStore
from phase7_zones.geometry import ground_point, inflate_polygon, point_in_polygon

from .config import QueueConfig


class QueueMonitor:
    def __init__(self, cfg: QueueConfig, store: EventStore, logger) -> None:
        self.cfg = cfg
        self.store = store
        self.log = logger
        self._last_check = 0.0
        self._last_alert: dict[int, float] = {}   # zone_id -> last alert ts
        self.checks_run = 0
        self.alerts_raised = 0

    def observe(self, camera_id: str, positions: dict[int, tuple[int, int, int, int]],
                frame_w: int, frame_h: int) -> None:
        now = time.time()
        if now - self._last_check < self.cfg.check_interval_s:
            return
        self._last_check = now

        zones = [z for z in self.store.list_zones(camera_id=camera_id, enabled_only=True)
                 if z.capacity]
        if not zones:
            return

        ground_points = [ground_point(bbox, frame_w, frame_h) for bbox in positions.values()]

        for zone in zones:
            polygon = inflate_polygon(zone.points, self.cfg.margin)
            count = sum(1 for pt in ground_points if point_in_polygon(pt, polygon))
            check = self.store.add_queue_check(
                zone_id=zone.id, camera_id=camera_id, ts=now, count=count, capacity=zone.capacity,
            )
            self.checks_run += 1
            self.log.info("queue check '%s': %d/%d", zone.name, count, zone.capacity)

            if count > zone.capacity:
                cooldown = zone.queue_cooldown_s or 60.0
                if now - self._last_alert.get(zone.id, 0.0) >= cooldown:
                    self._last_alert[zone.id] = now
                    alert = self.store.create_queue_alert(check, zone)
                    self.alerts_raised += 1
                    self.log.warning(
                        "QUEUE ALERT #%d '%s' - %d/%d over capacity",
                        alert.id, zone.name, count, zone.capacity,
                    )
