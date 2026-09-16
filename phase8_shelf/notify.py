"""Email for shelf alerts - reuses the same SMTP settings as Phase 6 (one set
of mail config for the whole platform), sent on a background thread so a slow
mail server never stalls the shelf-checking loop.
"""
from __future__ import annotations

import smtplib
import threading
from email.message import EmailMessage

from phase6_rules.config import Phase6Config


def notify_shelf_alert(alert, logger) -> None:
    cfg = Phase6Config.load()
    if not cfg.email_configured:
        return
    threading.Thread(target=_send, args=(cfg, alert, logger), name="shelf-alert-email", daemon=True).start()


def _send(cfg: Phase6Config, alert, logger) -> None:
    try:
        msg = EmailMessage()
        msg["Subject"] = f"[Reality Connector] Restock needed - {alert.shelf_name}"
        msg["From"] = cfg.smtp_from
        msg["To"] = ", ".join(cfg.alert_email_to)
        msg.set_content(
            f"Shelf:     {alert.shelf_name}\n"
            f"Camera:    {alert.camera_id}\n"
            f"Stock:     {alert.detected_count} / {alert.capacity} ({alert.pct_full * 100:.0f}%)\n"
            f"Alert id:  {alert.id} ({alert.alert_uid})\n"
        )
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=10) as s:
            if cfg.smtp_use_tls:
                s.starttls()
            if cfg.smtp_user:
                s.login(cfg.smtp_user, cfg.smtp_password or "")
            s.send_message(msg)
        logger.info("Shelf alert email sent for alert #%s", alert.id)
    except Exception as e:  # noqa: BLE001 - never let mail failures propagate
        logger.warning("Shelf alert email failed for alert #%s: %s", getattr(alert, "id", "?"), e)
