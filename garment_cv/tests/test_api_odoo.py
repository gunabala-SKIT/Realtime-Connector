"""The /api/odoo/* endpoints and the dashboard's Odoo tab, against a bridge talking to a fake Odoo."""
import logging
import tempfile
import unittest
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from garment_cv.backend.activities.config import ActivityConfig
from garment_cv.backend.activities.engine import ActivityEngine
from garment_cv.backend.api.dashboard import render_dashboard
from garment_cv.backend.api.routes import create_app
from garment_cv.backend.cameras.runner import CameraRunner
from garment_cv.backend.database.db import Database
from garment_cv.backend.events.store import EventStore
from garment_cv.backend.workers.store import WorkerStore
from garment_cv.backend.odoo_bridge.bridge import OdooBridge
from garment_cv.backend.odoo_bridge.client import OdooClient
from garment_cv.backend.odoo_bridge.config import BridgeConfig, parse_operation_map
from garment_cv.backend.odoo_bridge.tracker import FlowConfig

from .test_api_detection import cam_cfg
from .test_odoo_bridge import FakeOdoo

log = logging.getLogger("test")
log.addHandler(logging.NullHandler())


class OdooApi(unittest.TestCase):
    def setUp(self):
        self.fake = FakeOdoo()
        self.mo = self.fake.add_mo("WH/MO/1", ["Marking", "Cutting"])
        db = Database(Path(tempfile.mkdtemp()) / "t.db")
        cfg = BridgeConfig(mode="shadow", url="http://odoo.test", db="d", api_key="SECRET-KEY-XYZ", workcenter_code="cam1",
                           operation_map=parse_operation_map("Marking=MARKING,Cutting=CUTTING"), flow=FlowConfig(), poll_s=3600)
        client = OdooClient("http://odoo.test", "d", "SECRET-KEY-XYZ", transport=httpx.MockTransport(self.fake.handler))
        self.bridge = OdooBridge(cfg, db, log, client=client)
        app = create_app(CameraRunner(cam_cfg("D:/nope.mp4"), log), detector=None, zone_store=None, start_camera=False,
                         activity_engine=ActivityEngine(ActivityConfig.load(), on_tick=self.bridge.on_tick),
                         event_store=EventStore(db), worker_store=WorkerStore(db), odoo_bridge=self.bridge)
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(lambda: self.client.__exit__(None, None, None))

    def test_status_reports_mode_and_never_leaks_the_key(self):
        self.bridge.poll_once()
        r = self.client.get("/api/odoo/status")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["mode"], "shadow")
        self.assertEqual(r.json()["production"]["name"], "WH/MO/1")
        self.assertNotIn("SECRET-KEY-XYZ", r.text)

    def test_kill_switch_changes_mode_and_rejects_nonsense(self):
        self.assertEqual(self.client.post("/api/odoo/mode", json={"mode": "off"}).json()["mode"], "off")
        self.assertEqual(self.client.post("/api/odoo/mode", json={"mode": "banana"}).status_code, 400)

    def test_manual_override_needs_live_mode_and_an_armed_mo(self):
        r = self.client.post("/api/odoo/manual", json={"action": "start", "step": 0})
        self.assertEqual(r.status_code, 400)

    def test_audit_log_records_mode_changes(self):
        self.client.post("/api/odoo/mode", json={"mode": "live"})
        kinds = [a["kind"] for a in self.client.get("/api/odoo/audit").json()]
        self.assertIn("mode", kinds)

    def test_hold_endpoint(self):
        self.bridge.poll_once()
        self.assertEqual(self.client.post("/api/odoo/hold", json={"on": True}).status_code, 200)
        self.assertTrue(self.bridge.tracker.held)

    def test_dashboard_has_an_odoo_tab_and_it_renders_without_format_errors(self):
        html = self.client.get("/dashboard").text
        self.assertIn('data-view="odoo"', html)
        self.assertIn("/api/odoo/status", html)

    def test_dashboard_fills_every_placeholder_and_escapes_the_camera_name(self):
        page = render_dashboard(camera_name='<b>"Cam" & </script>', camera_id="a</script><script>x")
        self.assertNotRegex(page, r"__[A-Z][A-Z0-9_]*__")                # no unfilled placeholder
        self.assertNotIn("<b>", page)                                      # name is HTML-escaped
        self.assertNotIn("a</script><script>x", page)                      # id cannot close the script element
        self.assertIn("&lt;b&gt;", page)


if __name__ == "__main__":
    unittest.main()
