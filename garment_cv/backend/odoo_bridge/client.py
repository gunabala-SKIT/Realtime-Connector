"""Thin Odoo 19 JSON-2 client (`POST /json/2/<model>/<method>`, bearer API key).

JSON-2 rather than XML-RPC: verified against the real Odoo 19 checkout, XML-RPC raises a marshal error
when a method returns `None` (e.g. `mrp.workorder.button_start`) even though the action itself has
already committed - an error the caller can't tell apart from a real failure. JSON-2 returns `null`.

Errors are split into the two kinds the outbox needs to act on differently:
* `OdooTransient` - could not reach Odoo, timed out, or got a 5xx: retry later with back-off.
* `OdooRejected`  - Odoo understood and said no (bad key, access, UserError such as a blocked work
                    center, an action/wizard returned instead of `true`): retrying the same call
                    won't help; surface it to the operator.
"""
from __future__ import annotations

import httpx

MO_ACTIVE_STATES = ("progress", "confirmed", "to_close")


class OdooError(Exception):
    pass


class OdooTransient(OdooError):
    pass


class OdooRejected(OdooError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class OdooClient:
    def __init__(self, base_url: str, db: str, api_key: str, timeout_s: float = 15.0,
                 transport: httpx.BaseTransport | None = None) -> None:
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout_s, transport=transport,
            headers={"Authorization": f"bearer {api_key}", "X-Odoo-Database": db,
                     "Content-Type": "application/json; charset=utf-8"})

    def close(self) -> None:
        self._http.close()

    # ---------------------------------------------------------------------------- transport
    def call(self, model: str, method: str, ids: list[int] | None = None, **kwargs):
        body = dict(kwargs)
        if ids is not None:
            body["ids"] = ids
        try:
            r = self._http.post(f"/json/2/{model}/{method}", json=body)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise OdooTransient(f"cannot reach Odoo: {e.__class__.__name__}: {e}") from e
        if r.status_code >= 500:
            raise OdooTransient(f"Odoo server error {r.status_code}")
        if r.status_code >= 400:
            try:
                msg = (r.json() or {}).get("message") or r.text
            except ValueError:
                msg = r.text
            raise OdooRejected(f"{model}.{method}: {str(msg)[:300]}", r.status_code)
        if not r.content:                                    # a method that returns nothing
            return None
        try:
            return r.json()
        except ValueError as e:
            raise OdooTransient(f"unreadable reply from Odoo: {e}") from e

    # ------------------------------------------------------------------------------- queries
    def find_workcenter(self, code: str) -> dict | None:
        rows = self.call("mrp.workcenter", "search_read", domain=[["code", "=", code]],
                         fields=["id", "name", "code", "working_state"], limit=2)
        if len(rows) > 1:
            raise OdooRejected(f"more than one work center has code {code!r} - codes must be unique")
        return rows[0] if rows else None

    def open_workorders(self, workcenter_id: int) -> list[dict]:
        """Not-yet-finished work orders at this work center, with their MO state, in Odoo's own order."""
        return self.call("mrp.workorder", "search_read",
                         domain=[["workcenter_id", "=", workcenter_id], ["state", "not in", ["done", "cancel"]],
                                 ["production_state", "in", list(MO_ACTIVE_STATES)]],
                         fields=["id", "name", "state", "production_id", "production_state", "sequence"],
                         order="sequence, id")

    def workorders_of(self, production_id: int) -> list[dict]:
        return self.call("mrp.workorder", "search_read", domain=[["production_id", "=", production_id]],
                         fields=["id", "name", "state", "workcenter_id", "sequence"], order="sequence, id")

    def production_state(self, production_id: int) -> str | None:
        rows = self.call("mrp.production", "read", [production_id], fields=["state"])
        return rows[0]["state"] if rows else None

    # ------------------------------------------------------------------------------- actions
    def start_workorder(self, wo_id: int) -> None:
        self.call("mrp.workorder", "button_start", [wo_id])

    def finish_workorder(self, wo_id: int) -> None:
        self.call("mrp.workorder", "button_finish", [wo_id])

    def mark_production_done(self, mo_id: int) -> None:
        """`True` means done. Anything else is an Odoo wizard (consumption/backorder/lot) waiting for a
        human - treated as a rejection so it is shown to the operator rather than assumed finished."""
        res = self.call("mrp.production", "button_mark_done", [mo_id])
        if res is not True:
            kind = res.get("res_model") if isinstance(res, dict) else type(res).__name__
            raise OdooRejected(f"MO {mo_id} needs a manual step in Odoo before it can be marked done ({kind})")

    def post_note(self, model: str, rec_id: int, body: str) -> None:
        self.call(model, "message_post", [rec_id], body=body, message_type="comment",
                  subtype_xmlid="mail.mt_note")
