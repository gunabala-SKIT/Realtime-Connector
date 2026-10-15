"""The Odoo bridge end to end against a fake Odoo (httpx MockTransport) - no network, no real Odoo.

The fake mimics what was verified on a real Odoo 19 test database: JSON-2 endpoints, idempotent
start/finish, `button_mark_done` returning `true`, and (important) NO enforcement of operation order.
Failure injection covers the outage / rejection / supervisor-interferes cases.
"""
import json
import logging
import tempfile
import threading
import time
import unittest
from pathlib import Path

import httpx

from garment_cv.backend.database.db import Database
from garment_cv.backend.odoo_bridge.bridge import OdooBridge
from garment_cv.backend.odoo_bridge.client import OdooClient
from garment_cv.backend.odoo_bridge.config import BridgeConfig, parse_operation_map
from garment_cv.backend.odoo_bridge.tracker import FlowConfig

MARK, CUT, NONE = frozenset({"MARKING"}), frozenset({"CUTTING"}), frozenset()


class FakeOdoo:
    def __init__(self):
        self.workcenters = [{"id": 1, "name": "Live Camera 1", "code": "cam1", "working_state": "normal"}]
        self.mos = {}            # id -> {"name","state"}
        self.wos = {}            # id -> dict
        self.calls = []          # (model, method, ids)
        self.down = False
        self.gate = None         # a threading.Event: when set on the fake, requests hang until it is released
        self.reject = {}         # method -> message (Odoo says no)
        self.notes = []
        self._n = 100

    def add_mo(self, name, ops, state="progress", wc=1):
        mo = self._n = self._n + 1
        self.mos[mo] = {"name": name, "state": state}
        for i, op in enumerate(ops):
            wcid = op[1] if isinstance(op, tuple) else wc
            opname = op[0] if isinstance(op, tuple) else op
            self._n += 1
            self.wos[self._n] = {"id": self._n, "name": opname, "state": "ready" if i == 0 else "blocked",
                                 "production_id": mo, "workcenter_id": wcid, "sequence": i}
        return mo

    def wo_ids(self, mo):
        return [w["id"] for w in self.wos.values() if w["production_id"] == mo]

    def state(self, mo):
        return [w["state"] for w in self.wos.values() if w["production_id"] == mo]

    # ------------------------------------------------------------------------------ http layer
    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused")
        if self.gate is not None:
            self.gate.wait(10)
        _, _, _, model, method = request.url.path.split("/", 4)
        body = json.loads(request.content or b"{}")
        ids = body.get("ids")
        self.calls.append((model, method, ids))
        if method in self.reject:
            return httpx.Response(422, json={"message": self.reject[method]})
        return httpx.Response(200, content=json.dumps(getattr(self, "_" + method)(model, ids, body)).encode())

    def _wo_row(self, w):
        mo = self.mos[w["production_id"]]
        return {"id": w["id"], "name": w["name"], "state": w["state"], "sequence": w["sequence"],
                "production_id": [w["production_id"], mo["name"]], "production_state": mo["state"],
                "workcenter_id": [w["workcenter_id"], "wc"]}

    def _search_read(self, model, ids, body):
        if model == "mrp.workcenter":
            code = body["domain"][0][2]
            return [w for w in self.workcenters if w["code"] == code]
        dom = {d[0]: d for d in body["domain"]}
        rows = []
        for w in sorted(self.wos.values(), key=lambda w: (w["sequence"], w["id"])):
            if "workcenter_id" in dom and w["workcenter_id"] != dom["workcenter_id"][2]:
                continue
            if "production_id" in dom and w["production_id"] != dom["production_id"][2]:
                continue
            if "state" in dom and w["state"] in dom["state"][2]:
                continue
            r = self._wo_row(w)
            if "production_state" in dom and r["production_state"] not in dom["production_state"][2]:
                continue
            rows.append(r)
        return rows

    def _read(self, model, ids, body):
        return [{"id": i, "state": self.mos[i]["state"]} for i in ids]

    def _button_start(self, model, ids, body):
        for i in ids:
            if self.wos[i]["state"] not in ("done", "cancel"):
                self.wos[i]["state"] = "progress"        # NB: no check that the previous operation is done
        return None

    def _button_finish(self, model, ids, body):
        for i in ids:
            self.wos[i]["state"] = "done"
            nxt = [w for w in self.wos.values() if w["production_id"] == self.wos[i]["production_id"]
                   and w["state"] == "blocked"]
            if nxt:
                min(nxt, key=lambda w: w["sequence"])["state"] = "ready"
        return True

    def _button_mark_done(self, model, ids, body):
        for i in ids:
            self.mos[i]["state"] = "done"
            for w in self.wos.values():
                if w["production_id"] == i:
                    w["state"] = "done"
        return True

    def _message_post(self, model, ids, body):
        self.notes.append((model, ids, body.get("body")))
        return 1


class Clock:
    def __init__(self):
        self.t = 5_000.0

    def __call__(self):
        return self.t


FLOW = FlowConfig(start_hold_s=2, handoff_hold_s=3, min_active_s=10, quiet_s=45, hold_grace_s=3, max_gap_s=5)


class Rig:
    def __init__(self, mode="live", db_path=None, fake=None, ops=("Marking", "Cutting")):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = db_path or Path(self.tmp.name) / "t.db"
        self.fake = fake or FakeOdoo()
        self.clock = Clock()
        self.cam_t = 1000.0
        self.db = Database(self.db_path)
        self.cfg = BridgeConfig(mode=mode, url="http://odoo.test", db="d", api_key="k", workcenter_code="cam1",
                                operation_map=parse_operation_map("Marking=MARKING,Cutting=CUTTING"), flow=FLOW)
        self.bridge = self.make_bridge()
        self.mo = self.fake.add_mo("WH/MO/1", list(ops)) if ops else None

    def make_bridge(self):
        client = OdooClient("http://odoo.test", "d", "k", transport=httpx.MockTransport(self.fake.handler))
        return OdooBridge(self.cfg, self.db, logging.getLogger("t"), client=client, clock=self.clock)

    def seconds(self, s, acts=NONE, poll_every=1.0):
        """Advance camera time (and the bridge's wall clock), polling Odoo like the background thread would."""
        for _ in range(int(round(s / 0.5))):
            self.cam_t += 0.5
            self.clock.t += 0.5
            self.bridge.on_tick("cam1", self.cam_t, acts)
            if (self.cam_t * 2) % int(poll_every * 2) == 0:
                self.bridge.poll_once()

    def close(self):
        self.db.close()
        self.tmp.cleanup()


class FullFlow(unittest.TestCase):
    def test_marking_then_cutting_then_everything_done(self):
        r = Rig()
        r.bridge.poll_once()                                       # arms on the MO
        self.assertEqual(r.bridge.production_id, r.mo)
        r.seconds(4, MARK)
        self.assertEqual(r.fake.state(r.mo), ["progress", "blocked"])
        r.seconds(4, CUT)                                          # hand-off
        self.assertEqual(r.fake.state(r.mo), ["done", "progress"])
        r.seconds(12, CUT)
        r.seconds(40, NONE)
        self.assertEqual(r.fake.state(r.mo), ["done", "progress"]) # 40 s < 45 s quiet
        r.seconds(10, NONE)
        self.assertEqual(r.fake.state(r.mo), ["done", "done"])
        self.assertEqual(r.fake.mos[r.mo]["state"], "done")
        r.close()

    def test_picking_up_scissors_never_finishes_anything(self):
        r = Rig()
        r.bridge.poll_once()
        r.seconds(4, MARK)
        r.seconds(60, NONE)                                        # scissors picked up and put down: no CUTTING
        self.assertEqual(r.fake.state(r.mo), ["progress", "blocked"])
        r.close()

    def test_nothing_happens_for_an_mo_that_was_not_started(self):
        r = Rig()
        r.fake.mos[r.mo]["state"] = "confirmed"
        r.bridge.poll_once()
        r.seconds(10, MARK)
        self.assertEqual(r.fake.state(r.mo), ["ready", "blocked"])
        self.assertIsNone(r.bridge.production_id)
        r.close()

    def test_audit_notes_are_posted_on_the_manufacturing_order(self):
        r = Rig()
        r.bridge.poll_once()
        r.seconds(4, MARK)
        self.assertTrue(any(n[0] == "mrp.production" and n[1] == [r.mo] and "start" in (n[2] or "")
                            and "Marking" in (n[2] or "") for n in r.fake.notes))
        r.close()


class Modes(unittest.TestCase):
    def test_shadow_decides_and_logs_but_never_writes(self):
        r = Rig(mode="shadow")
        r.bridge.poll_once()
        r.seconds(4, MARK)
        r.seconds(4, CUT)
        self.assertEqual(r.fake.state(r.mo), ["ready", "blocked"])
        self.assertFalse([c for c in r.fake.calls if c[1] in ("button_start", "button_finish", "button_mark_done")])
        kinds = [(a["kind"], a["detail"]) for a in r.bridge.audit_log()]
        self.assertTrue(any(k == "decision" and "START Marking" in d for k, d in kinds))
        self.assertTrue(any(k == "decision" and "FINISH Marking" in d for k, d in kinds))
        r.close()

    def test_kill_switch_stops_all_writes_immediately(self):
        r = Rig()
        r.bridge.poll_once()
        r.seconds(4, MARK)
        r.bridge.set_mode("off")
        r.seconds(10, CUT)
        self.assertEqual(r.fake.state(r.mo), ["progress", "blocked"])
        self.assertEqual(r.bridge.status()["mode"], "off")
        r.close()

    def test_mode_survives_a_restart(self):
        r = Rig()
        r.bridge.set_mode("shadow")
        again = r.make_bridge()
        self.assertEqual(again.mode, "shadow")
        r.close()

    def test_shadow_mode_follows_a_person_changing_odoo_but_keeps_its_own_decisions(self):
        r = Rig(mode="shadow")
        r.bridge.poll_once()
        r.seconds(4, MARK)                                         # the bridge decides Marking started (shadow)
        r.seconds(3, NONE)
        self.assertEqual(r.bridge.tracker.states, ["active", "pending"])   # polls did not erase the decision
        w = r.fake.wo_ids(r.mo)
        r.fake._button_start("mrp.workorder", [w[1]], {})           # a person starts Cutting by hand in Odoo
        r.bridge.poll_once()
        self.assertEqual(r.bridge.tracker.states, ["pending", "active"])
        self.assertTrue(any("adopted Odoo states" in a["detail"] for a in r.bridge.audit_log()))
        r.close()

    def test_manual_start_in_odoo_before_any_camera_activity_is_shown(self):
        r = Rig(mode="shadow")
        r.bridge.poll_once()
        r.fake._button_start("mrp.workorder", [r.fake.wo_ids(r.mo)[0]], {})
        r.bridge.poll_once()
        self.assertEqual(r.bridge.status()["flow"]["steps"][0]["state"], "active")
        r.close()


class DecisionsAreSentAtOnce(unittest.TestCase):
    def test_a_decision_wakes_the_sender_instead_of_waiting_for_the_next_poll(self):
        r = Rig()
        r.bridge.poll_once()
        r.bridge._wake.clear()
        r.seconds(4, MARK)                                      # the bridge decides to start Marking
        self.assertTrue(r.bridge._wake.is_set())
        r.close()


class Failures(unittest.TestCase):
    def test_odoo_outage_delays_the_action_and_it_is_applied_once_when_back(self):
        r = Rig()
        r.bridge.poll_once()
        r.fake.down = True
        r.seconds(4, MARK)                                         # decision made while Odoo is down
        self.assertEqual(r.fake.state(r.mo), ["ready", "blocked"])
        self.assertEqual(r.bridge.status()["queue"].get("pending"), 1)
        self.assertFalse(r.bridge.status()["odoo_ok"])
        r.fake.down = False
        r.seconds(70, MARK)                                        # past the retry back-off
        self.assertEqual(r.fake.state(r.mo), ["progress", "blocked"])
        starts = [c for c in r.fake.calls if c[1] == "button_start"]
        self.assertEqual(len(starts), 1)                           # not duplicated
        r.close()

    def test_rejection_is_not_retried_and_blocks_later_actions_until_operator_acts(self):
        r = Rig()
        r.bridge.poll_once()
        r.fake.reject["button_start"] = "Please unblock the work center to start the work order"
        r.seconds(4, MARK)
        st = r.bridge.status()
        self.assertEqual(len(st["failed"]), 1)
        self.assertIn("unblock", st["failed"][0]["last_error"])
        attempts = len([c for c in r.fake.calls if c[1] == "button_start"])
        r.seconds(20, MARK)
        self.assertEqual(len([c for c in r.fake.calls if c[1] == "button_start"]), attempts)   # no blind retries
        del r.fake.reject["button_start"]
        r.bridge.retry_failed()
        r.seconds(3, MARK)
        self.assertEqual(r.fake.state(r.mo), ["progress", "blocked"])
        r.close()

    def test_restart_with_pending_actions_applies_them(self):
        r = Rig()
        r.bridge.poll_once()
        r.fake.down = True
        r.seconds(4, MARK)
        r.fake.down = False
        r.bridge = r.make_bridge()                                 # process restarted; same database file
        r.clock.t += 120
        r.bridge.poll_once()
        r.bridge.poll_once()
        self.assertEqual(r.fake.state(r.mo), ["progress", "blocked"])
        r.close()

    def test_mo_done_refused_if_another_work_centers_operation_is_unfinished(self):
        r = Rig(ops=("Marking", "Cutting", ("Stitching", 2)))
        r.bridge.poll_once()
        r.seconds(4, MARK)
        r.seconds(4, CUT)
        r.seconds(12, CUT)
        r.seconds(60, NONE)
        self.assertEqual(r.fake.state(r.mo)[:2], ["done", "done"])
        self.assertNotEqual(r.fake.mos[r.mo]["state"], "done")     # other work center's op is not forced closed
        self.assertIn("Stitching", r.bridge.status()["failed"][0]["last_error"])
        r.close()


class OneMoPerCamera(unittest.TestCase):
    def test_two_started_mos_is_a_conflict_and_nothing_is_written(self):
        r = Rig()
        r.fake.add_mo("WH/MO/2", ["Marking", "Cutting"])
        r.bridge.poll_once()
        self.assertIn("more than one MO", r.bridge.status()["conflict"])
        r.seconds(10, MARK)
        self.assertFalse([c for c in r.fake.calls if c[1] == "button_start"])
        r.close()

    def test_conflict_clears_when_one_mo_is_finished(self):
        r = Rig()
        mo2 = r.fake.add_mo("WH/MO/2", ["Marking", "Cutting"])
        r.bridge.poll_once()
        r.fake.mos[mo2]["state"] = "cancel"
        r.bridge.poll_once()
        self.assertIsNone(r.bridge.status()["conflict"])
        self.assertEqual(r.bridge.production_id, r.mo)
        r.close()

    def test_unmapped_operation_is_flagged_not_guessed(self):
        r = Rig(ops=("Marking", "Ironing"))
        r.bridge.poll_once()
        self.assertIn("Ironing", r.bridge.status()["conflict"])
        r.seconds(10, MARK)
        self.assertFalse([c for c in r.fake.calls if c[1] == "button_start"])
        r.close()

    def test_unknown_work_center_is_reported(self):
        r = Rig()
        r.cfg.workcenter_code = "nope"
        r.bridge = r.make_bridge()
        r.bridge.poll_once()
        self.assertIn("nope", r.bridge.status()["last_error"])
        r.close()


class OdooIsTheSourceOfTruth(unittest.TestCase):
    def test_supervisor_finishing_marking_in_odoo_is_adopted(self):
        r = Rig()
        r.bridge.poll_once()
        r.seconds(4, MARK)
        w = r.fake.wo_ids(r.mo)
        r.fake._button_finish("mrp.workorder", [w[0]], {})          # a supervisor presses Done in Odoo
        r.bridge.poll_once()
        self.assertEqual(r.bridge.tracker.states, ["finished", "pending"])
        r.seconds(5, CUT)                                          # cutting then starts normally
        self.assertEqual(r.fake.state(r.mo), ["done", "progress"])
        r.close()

    def test_operator_manual_override_start_and_finish(self):
        r = Rig()
        r.bridge.poll_once()
        r.bridge.manual("start", 1)                                # jump straight to Cutting: Marking is closed first
        r.bridge.poll_once()
        self.assertEqual(r.fake.state(r.mo), ["done", "progress"])
        r.bridge.manual("finish", 1)
        r.bridge.poll_once()
        self.assertEqual(r.fake.mos[r.mo]["state"], "done")
        r.close()

    def test_hold_prevents_auto_finish(self):
        r = Rig()
        r.bridge.poll_once()
        r.seconds(4, MARK)
        r.seconds(4, CUT)
        r.seconds(12, CUT)
        r.bridge.hold(True)
        r.seconds(120, NONE)
        self.assertEqual(r.fake.state(r.mo), ["done", "progress"])
        r.bridge.hold(False)
        r.seconds(2, NONE)
        self.assertEqual(r.fake.state(r.mo), ["done", "done"])
        r.close()


class SlowOdooNeverBlocksTheCamera(unittest.TestCase):
    def test_on_tick_returns_immediately_while_a_poll_is_hanging_inside_odoo(self):
        r = Rig()
        r.bridge.poll_once()                                    # armed
        r.fake.gate = threading.Event()
        t = threading.Thread(target=r.bridge.poll_once)
        t.start()
        time.sleep(0.3)                                         # the poll is now stuck waiting for Odoo
        t0 = time.time()
        for i in range(20):
            r.cam_t += 0.5
            r.bridge.on_tick("cam1", r.cam_t, MARK)
        elapsed = time.time() - t0
        r.fake.gate.set()
        t.join(5)
        self.assertLess(elapsed, 1.0)                           # the detection thread was never made to wait
        r.close()


class Config(unittest.TestCase):
    def test_operation_map_parsing(self):
        self.assertEqual(parse_operation_map("Marking=MARKING, cutting = cutting,bad,=x"),
                         {"marking": "MARKING", "cutting": "CUTTING"})

    def test_api_key_never_appears_in_status_or_repr(self):
        r = Rig()
        r.cfg.api_key = "SECRET-TOKEN-123"
        self.assertNotIn("SECRET-TOKEN-123", repr(r.cfg))
        self.assertNotIn("SECRET-TOKEN-123", json.dumps(r.bridge.status()))
        r.close()


if __name__ == "__main__":
    unittest.main()
