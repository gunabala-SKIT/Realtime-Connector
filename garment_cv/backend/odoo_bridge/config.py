"""Odoo bridge settings. Everything operational is a knob here (spec: configurable, not hard-coded).

The API key is read from the environment / .env only and is never logged or returned by the API.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..config import _get, ensure_env_loaded
from .tracker import FlowConfig

MODES = ("off", "shadow", "live")


def _f(name: str, default: float) -> float:
    try:
        return float(_get(name, str(default)))
    except (TypeError, ValueError):
        return default


def parse_operation_map(text: str) -> dict[str, str]:
    """'Marking=MARKING,Cutting=CUTTING' -> {'marking': 'MARKING', 'cutting': 'CUTTING'} (operation names
    compared case-insensitively - people type 'marking' and 'Marking' interchangeably in Odoo)."""
    out: dict[str, str] = {}
    for part in (text or "").split(","):
        if "=" in part:
            op, act = part.split("=", 1)
            if op.strip() and act.strip():
                out[op.strip().lower()] = act.strip().upper()
    return out


@dataclass
class BridgeConfig:
    mode: str                         # off | shadow (decide + log, never write to Odoo) | live
    url: str
    db: str
    api_key: str = field(repr=False)
    workcenter_code: str              # Odoo work center `code` that stands for this camera
    operation_map: dict[str, str]
    flow: FlowConfig
    poll_s: float = 3.0               # how often to reconcile with Odoo and drain the outbox
    timeout_s: float = 15.0
    arm_on_confirmed: bool = False    # also act on an MO that is only "confirmed" (not yet Start-ed)
    post_notes: bool = True           # leave an audit note on the work order / MO in Odoo

    @property
    def configured(self) -> bool:
        return bool(self.url and self.db and self.api_key and self.workcenter_code)

    @classmethod
    def load(cls, camera_id: str = "") -> "BridgeConfig":
        ensure_env_loaded()
        mode = (_get("GARMENT_ODOO_MODE", "off") or "off").strip().lower()
        d = FlowConfig()
        return cls(
            mode=mode if mode in MODES else "off",
            url=(_get("GARMENT_ODOO_URL", "") or "").strip(),
            db=(_get("GARMENT_ODOO_DB", "") or "").strip(),
            api_key=(_get("GARMENT_ODOO_API_KEY", "") or "").strip(),
            workcenter_code=(_get("GARMENT_ODOO_WORKCENTER", camera_id) or camera_id).strip(),
            operation_map=parse_operation_map(_get("GARMENT_ODOO_OPERATION_MAP", "Marking=MARKING,Cutting=CUTTING") or ""),
            flow=FlowConfig(
                start_hold_s=_f("GARMENT_ODOO_START_HOLD_S", d.start_hold_s),
                handoff_hold_s=_f("GARMENT_ODOO_HANDOFF_HOLD_S", d.handoff_hold_s),
                min_active_s=_f("GARMENT_ODOO_MIN_ACTIVE_S", d.min_active_s),
                quiet_s=_f("GARMENT_ODOO_QUIET_S", d.quiet_s),
                hold_grace_s=_f("GARMENT_ODOO_HOLD_GRACE_S", d.hold_grace_s),
                max_gap_s=_f("GARMENT_ODOO_MAX_GAP_S", d.max_gap_s)),
            poll_s=_f("GARMENT_ODOO_POLL_S", 3.0),
            timeout_s=_f("GARMENT_ODOO_TIMEOUT_S", 15.0),
            arm_on_confirmed=(_get("GARMENT_ODOO_ARM_ON_CONFIRMED", "false") or "").lower() in ("1", "true", "yes", "on"),
            post_notes=(_get("GARMENT_ODOO_POST_NOTES", "true") or "").lower() in ("1", "true", "yes", "on"),
        )
