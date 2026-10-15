# Odoo Manufacturing bridge - runbook

The camera drives Odoo work orders: marking starts operation 1, cutting hands off to operation 2,
finished cutting closes operation 2 and the manufacturing order (MO).

## One-time setup in Odoo (19)

1. **Work Orders enabled** (Manufacturing > Configuration > Settings > Work Orders).
2. **Work Center** for the camera: set its **Code** to the camera id (`GARMENT_CAMERA_ID`, default
   `camera-1`) - or set `GARMENT_ODOO_WORKCENTER` to whatever code you used. Codes must be unique.
3. **Bill of Materials**: product `Fabric piece`, component `Fabric`, operations `Marking` then `Cutting`
   (in that order), both on that work center. Operation names must match `GARMENT_ODOO_OPERATION_MAP`.
4. **Integration user + API key**: a dedicated user with Manufacturing access, linked to an Employee
   (enterprise `mrp_workorder` requires one to start a work order). Create an API key under
   *My Profile > Account Security* and put it in `garment_cv/configs/.env` as `GARMENT_ODOO_API_KEY`.
5. Enterprise **quality checks** on these operations that need a human (measurements, photos) cannot be
   auto-finished: Odoo refuses and the bridge shows the rejection.

## Rolling out safely

1. Set `GARMENT_ODOO_URL`, `GARMENT_ODOO_DB`, `GARMENT_ODOO_API_KEY`, `GARMENT_ODOO_MODE=shadow`.
2. Start garment_cv, open the dashboard's **Odoo** tab. Create an MO, **Confirm** and **Start** it.
   The tab shows "armed" and the operations. Do the real work in front of the camera.
3. In **shadow** mode the audit log shows what the bridge *would* have done (`decision (shadow)`),
   and Odoo is untouched. Check it matches what really happened, over several real jobs.
4. Switch to **Live** (button on the tab). From then on it updates Odoo.

## How decisions are made

| Event | Needs |
|---|---|
| Start Marking | MARKING confirmed (3 s by the engine) and held `START_HOLD_S` more |
| Marking done + Cutting start | CUTTING confirmed and held `HANDOFF_HOLD_S` |
| Cutting done + MO done | at least `MIN_ACTIVE_S` of cutting in total, then `QUIET_S` with no cutting, camera frames arriving throughout |

Picking up or putting down scissors never confirms CUTTING (the engine needs scissors **on cloth and
moving**), so it cannot finish anything. A pause shorter than `QUIET_S` does not finish the job; if
cutting resumes during the countdown the same work order simply continues. The dashboard shows
"Finishing in Ns". A camera outage or stalled app pauses the countdown - it is never read as "quiet".

## When something looks wrong

| Dashboard says | Meaning / do |
|---|---|
| *more than one MO is in progress at work center X* | One MO per camera. Finish/cancel the others in Odoo; it resumes by itself. |
| *operation(s) [...] have no camera activity mapped* | Add it to `GARMENT_ODOO_OPERATION_MAP` or fix the operation name. |
| *no work center with code X* | The Odoo work center `Code` doesn't match. |
| Odoo **unreachable** | Actions queue (`Queued`) and are sent when Odoo is back; nothing is lost across restarts. |
| a **Failed** action | Odoo refused (e.g. work center blocked, quality check pending, MO needs a wizard). Fix it in Odoo, then **Retry failed** (or **Discard failed** if it was done by hand). Later actions for that MO wait. |
| *MO has unfinished operations elsewhere* | The last Cutting finished but another work center's operation is still open; the MO is deliberately not forced done. |

Anything can be overridden by hand: **Hold auto-finish**, **Start/Finish <operation>** (closes earlier
operations first), or change it directly in Odoo - the bridge follows Odoo's state within `POLL_S`.
**Off** is the kill switch: it stops all writes immediately.

## Safety properties (each covered by tests)

- Odoo is the source of truth; the bridge re-reads work-order states every poll.
- Actions go through a persistent queue; Odoo's start/finish are idempotent, so retries are safe.
- A rejection is never retried blindly and never silently dropped.
- Operation order is enforced by the bridge (Odoo itself allows starting Cutting before Marking is done).
- The bridge never raises into the detection thread; the camera pipeline is unaffected by Odoo failures.
- The API key is never logged or returned by the API.

## Known limits

- The camera can tell activity from non-activity; it cannot count pieces. Quantity stays as planned on
  the MO.
- The dashboard endpoints have no authentication (like the rest of this app) - keep `GARMENT_HOST` on
  localhost or put it behind your network controls.
- Calibrate `QUIET_S` / `MIN_ACTIVE_S` to your real jobs in shadow mode: a worker who pauses longer than
  `QUIET_S` mid-job would be finished early.
