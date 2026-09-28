import unittest
from dataclasses import replace

from fastapi.testclient import TestClient

from manufacturing_intel.config import Settings
from manufacturing_intel.server import create_app
from manufacturing_intel.store import Store

from .common import Fx, M, T0
from .test_analytics import run_unit


def settings(**kw) -> Settings:
    base = Settings(db_path=":memory:", host="127.0.0.1", port=8100, api_key=None, admin_remote=False, alert_interval_s=30)
    return replace(base, **kw)


def make(**kw):
    """App over a store that already holds the reference factory and some production 'now'."""
    import time
    now = time.time()
    fx = Fx(now=now, t0=now - 3 * 3600)
    fx.batch(at_minute=0)
    for i in range(12):
        run_unit(fx, f"GEAR-{i + 1:06d}", 5 + i * 3.0, fate="finished" if i < 10 else ("reject", "Grinding", "surface_defect"))
    app = create_app(settings(**kw), fx.store, start_background=False)
    return fx, app


class Basics(unittest.TestCase):
    def setUp(self):
        self.fx, self.app = make()
        self.c = TestClient(self.app)
        self.line = self.fx.line

    def test_health_and_config(self):
        self.assertTrue(self.c.get("/api/health").json()["ok"])
        cfg = self.c.get("/api/config").json()
        self.assertEqual(len(cfg["stations"]), 12)
        self.assertEqual({p["code"] for p in cfg["products"]}, {"GEAR", "SHAFT", "BRACKET"})
        self.assertIn("surface_defect", cfg["reject_reasons"])

    def test_overview_has_everything_the_dashboard_needs(self):
        o = self.c.get("/api/overview", params={"period": "today", "line_id": self.line}).json()
        self.assertEqual(o["line"]["name"], "Line A")
        self.assertEqual(o["kpis"]["issued"], 12)
        self.assertEqual((o["kpis"]["finished"], o["kpis"]["rejected"]), (10, 2))
        names = [s["name"] for s in o["stations"]]
        self.assertEqual(names[0], "Raw Material")
        self.assertEqual(names[-1], "Finished Goods")
        self.assertIn("previous", o)
        self.assertIn("data_quality", o)

    def test_bad_periods_and_ids_are_client_errors(self):
        self.assertEqual(self.c.get("/api/overview", params={"period": "fortnight"}).status_code, 400)
        self.assertEqual(self.c.get("/api/overview", params={"period": "custom"}).status_code, 400)
        self.assertEqual(self.c.get("/api/overview", params={"line_id": 999}).status_code, 404)
        self.assertEqual(self.c.get("/api/units/NOPE-1").status_code, 404)
        self.assertEqual(self.c.get("/api/breakdown", params={"by": "colour"}).status_code, 422)

    def test_units_journey_and_lists(self):
        j = self.c.get("/api/units/gear-000001").json()
        self.assertEqual(j["unit"]["status"], "finished")
        self.assertEqual(len(j["visits"]), 8)
        lst = self.c.get("/api/units", params={"status": "rejected"}).json()
        self.assertEqual(lst["total"], 2)
        self.assertEqual(len(self.c.get("/api/batches").json()), 1)
        flow = self.c.get(f"/api/batches/{self.c.get('/api/batches').json()[0]['id']}/flow").json()
        self.assertEqual(flow["stages"][0]["station"], "Raw Material")

    def test_breakdown_trend_rejections_compare(self):
        rows = self.c.get("/api/breakdown", params={"by": "day", "period": "last_7d", "line_id": self.line}).json()
        self.assertEqual(sum(r["started"] for r in rows), 12)
        self.assertTrue(self.c.get("/api/trend", params={"period": "today"}).json())
        rej = self.c.get("/api/rejections", params={"period": "last_7d"}).json()
        self.assertEqual(rej[0]["station"], "Grinding")
        cmp = self.c.get("/api/compare", params={"a": "today", "b": "yesterday", "line_id": self.line}).json()
        self.assertTrue(any(r["metric"] == "finished" for r in cmp["kpis"]))

    def test_assistant(self):
        r = self.c.post("/api/assistant", json={"question": "How many products were rejected today?"}).json()
        self.assertEqual(r["intent"], "rejected")
        self.assertEqual(self.c.post("/api/assistant", json={"question": "x" * 501}).status_code, 422)
        self.assertTrue(self.c.get("/api/assistant/examples").json())

    def test_alerts_endpoint_evaluates_and_acks(self):
        self.assertIn("opened", self.c.post("/api/alerts/evaluate").json())
        self.assertIsInstance(self.c.get("/api/alerts").json(), list)
        self.assertEqual(self.c.post("/api/alerts/99999/ack").status_code, 400)


class FirstRun(unittest.TestCase):
    """A brand-new install: nothing configured yet. Nothing may 500."""

    def setUp(self):
        self.c = TestClient(create_app(settings(), Store(":memory:"), start_background=False))

    def test_empty_install_is_usable(self):
        cfg = self.c.get("/api/config").json()
        self.assertEqual((cfg["lines"], cfg["stations"], cfg["cameras"]), ([], [], []))
        self.assertEqual(self.c.get("/api/overview").status_code, 409)                 # says why, doesn't crash
        self.assertIn("configured", self.c.get("/api/overview").json()["detail"])
        self.assertEqual(self.c.get("/api/units").json()["total"], 0)
        self.assertEqual(self.c.get("/api/batches").json(), [])
        self.assertEqual(self.c.get("/api/breakdown", params={"by": "day"}).json(), [])
        self.assertEqual(self.c.get("/api/trend").status_code, 200)
        self.assertEqual(self.c.get("/api/rejections").json(), [])
        self.assertEqual(self.c.get("/api/kpis").json()["finished"], 0)
        self.assertEqual(self.c.post("/api/alerts/evaluate").json()["active"], 0)
        self.assertEqual(self.c.post("/api/assistant", json={"question": "how many were completed today?"}).status_code, 200)
        self.assertEqual(self.c.post("/api/assistant", json={"question": "why is production slow"}).status_code, 200)

    def test_it_can_be_configured_from_nothing(self):
        line = self.c.post("/api/lines", json={"name": "L1"}).json()["id"]
        prod = self.c.post("/api/products", json={"code": "W", "name": "Widget", "target_cycle_min": 30}).json()["id"]
        ids = [self.c.post("/api/stations", json={"line_id": line, "name": n, "kind": k}).json()["id"]
               for n, k in (("In", "source"), ("Press", "process"), ("Out", "sink"))]
        r = self.c.put("/api/routes", json={"line_id": line, "product_id": prod, "station_ids": ids})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.c.post("/api/ingest/event", json={"type": "enter", "station_id": ids[1]}).json()["result"], "applied")   # auto-issue
        o = self.c.get("/api/overview").json()
        self.assertEqual((o["kpis"]["issued"], o["kpis"]["in_production"]), (1, 1))


class Configuration(unittest.TestCase):
    def setUp(self):
        self.fx, self.app = make()
        self.c = TestClient(self.app)
        self.line = self.fx.line

    def test_station_changes_and_validation(self):
        sid = self.fx.st["Drilling"]
        ok = self.c.patch(f"/api/stations/{sid}", json={"expected_wait_qty": 20, "capacity": 8}).json()
        self.assertEqual((ok["expected_wait_qty"], ok["capacity"]), (20, 8))
        self.assertEqual(self.c.patch(f"/api/stations/{sid}", json={"zone": [[0, 0], [2, 0], [1, 1]]}).status_code, 400)
        self.assertEqual(self.c.patch(f"/api/stations/{sid}", json={"expected_wait_qty": -1}).status_code, 422)
        z = self.c.patch(f"/api/stations/{sid}", json={"zone": [[0.1, 0.1], [0.5, 0.1], [0.5, 0.5]]}).json()
        self.assertEqual(len(z["zone"]), 3)
        self.assertEqual(self.c.post("/api/stations", json={"line_id": self.line, "name": "Cutting"}).status_code, 400)
        self.assertEqual(self.c.delete(f"/api/stations/{sid}").status_code, 400)          # used by routes

    def test_batch_and_route_rules(self):
        p = self.fx.ids["products"]["SHAFT"]
        b = self.c.post("/api/batches", json={"product_id": p, "line_id": self.line, "qty_planned": 50}).json()
        self.assertTrue(b["code"].startswith("BATCH-"))
        self.assertEqual(self.c.post("/api/batches", json={"product_id": p, "line_id": self.line, "qty_planned": 0}).status_code, 422)
        self.assertEqual(self.c.post(f"/api/batches/{b['id']}/close").json()["status"], "closed")
        r = self.c.put("/api/routes", json={"line_id": self.line, "product_id": p, "station_ids": [1, 2]})
        self.assertEqual(r.status_code, 422)

    def test_camera_crud_hides_credentials(self):
        cam = self.c.post("/api/cameras", json={"name": "Dock", "source": "rtsp://admin:hunter2@10.0.0.9/stream", "line_id": self.line}).json()
        self.assertNotIn("hunter2", str(cam))
        self.assertIn("admin:***@", cam["source"])
        self.assertNotIn("hunter2", self.c.get("/api/config").text)
        self.assertNotIn("hunter2", self.c.get("/api/cameras").text)
        self.assertEqual(self.c.post("/api/cameras", json={"name": "Dock", "source": "x"}).status_code, 400)
        self.assertEqual(self.c.post("/api/cameras", json={"name": "B", "source": "x", "detector": "magic"}).status_code, 400)
        self.assertEqual(self.c.delete(f"/api/cameras/{cam['id']}").json(), {"ok": True})


class Ingest(unittest.TestCase):
    def setUp(self):
        self.fx, self.app = make()
        self.c = TestClient(self.app)

    def ev(self, **kw):
        body = {"type": "issue", "station_id": self.fx.st["Raw Material"], "product": "GEAR", **kw}
        return self.c.post("/api/ingest/event", json=body)

    def test_event_roundtrip_by_id_and_by_name(self):
        r = self.ev(unit_code="GEAR-777001").json()
        self.assertEqual(r["result"], "applied")
        r2 = self.c.post("/api/ingest/event", json={"type": "enter", "line_id": self.fx.line, "station": "cutting",
                                                    "unit_code": "GEAR-777001"}).json()
        self.assertEqual(r2["result"], "applied")
        self.assertEqual(self.c.get("/api/units/GEAR-777001").json()["visits"][1]["outcome"], "processing")

    def test_bad_events_report_why(self):
        r = self.c.post("/api/ingest/event", json={"type": "teleport", "station_id": self.fx.st["Cutting"]})
        self.assertEqual(r.status_code, 422)
        self.assertIn("type must be one of", r.json()["detail"])
        r = self.c.post("/api/ingest/event", json={"type": "enter", "station_id": self.fx.st["Drilling"], "unit_code": "NOPE-9"})
        self.assertEqual(r.json()["result"], "unmatched")                     # accepted and logged, just not placed
        self.assertEqual(self.c.post("/api/ingest/event", json={"type": "enter"}).status_code, 422)

    def test_batch_ingest(self):
        items = [{"type": "issue", "station_id": self.fx.st["Raw Material"], "product": "GEAR", "unit_code": f"GEAR-88{i:04d}"}
                 for i in range(5)]
        out = self.c.post("/api/ingest/events", json=items).json()
        self.assertEqual(out["summary"], {"applied": 5})
        self.assertEqual(self.c.post("/api/ingest/events", json=items * 101).status_code, 413)

    def test_operator_events_are_tagged(self):
        self.c.post("/api/operator/event", json={"type": "issue", "station_id": self.fx.st["Raw Material"],
                                                 "product": "GEAR", "unit_code": "GEAR-999001"})
        src = self.fx.store.q1("SELECT source FROM events WHERE unit_code='GEAR-999001'")["source"]
        self.assertEqual(src, "operator")

    def test_recent_events_show_refusals(self):
        self.ev(unit_code="GEAR-000001")                                        # duplicate issue
        rows = self.c.get("/api/events", params={"result": "ignored", "limit": 5}).json()
        self.assertTrue(rows and rows[0]["result"] == "ignored")


class AccessRules(unittest.TestCase):
    def test_remote_clients_cannot_change_configuration(self):
        fx, app = make()
        remote = TestClient(app, client=("10.0.0.5", 50000))
        self.assertEqual(remote.get("/api/config").status_code, 200)                       # reading is open
        r = remote.post("/api/lines", json={"name": "Hacked"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(remote.post("/api/operator/event", json={"type": "issue", "station_id": 1}).status_code, 403)
        self.assertEqual(remote.post("/api/alerts/evaluate").status_code, 403)
        self.assertEqual(len(fx.store.list_lines()), 1)

    def test_remote_admin_can_be_enabled_explicitly(self):
        fx, app = make(admin_remote=True)
        self.assertEqual(TestClient(app, client=("10.0.0.5", 1)).post("/api/lines", json={"name": "Line Z"}).status_code, 200)

    def test_remote_ingest_needs_a_key(self):
        fx, app = make()
        remote = TestClient(app, client=("10.0.0.5", 50000))
        body = {"type": "issue", "station_id": fx.st["Raw Material"], "product": "GEAR"}
        self.assertEqual(remote.post("/api/ingest/event", json=body).status_code, 403)     # no key configured -> local only

    def test_ingest_key_is_enforced_everywhere_when_set(self):
        fx, app = make(api_key="s3cret")
        body = {"type": "issue", "station_id": fx.st["Raw Material"], "product": "GEAR", "unit_code": "GEAR-555001"}
        for client in (TestClient(app), TestClient(app, client=("10.0.0.5", 1))):
            self.assertEqual(client.post("/api/ingest/event", json=body).status_code, 401)
            self.assertEqual(client.post("/api/ingest/event", json=body, headers={"X-API-Key": "wrong"}).status_code, 401)
        good = TestClient(app, client=("10.0.0.5", 1)).post("/api/ingest/event", json=body, headers={"X-API-Key": "s3cret"})
        self.assertEqual(good.status_code, 200)
        self.assertEqual(good.json()["result"], "applied")
        self.assertNotIn("s3cret", TestClient(app).get("/api/health").text)


if __name__ == "__main__":
    unittest.main()
