"""Email notification for alerts (BRD FR-021). Best-effort and non-blocking:
a slow or misconfigured mail server must never stall the vision pipeline.
"""
from __future__ import annotations

import smtplib
import threading
import time
from email.message import EmailMessage

from .config import Phase6Config


def notify_alert(cfg: Phase6Config, alert, event, logger) -> None:
    if not cfg.email_configured:
        return
    threading.Thread(
        target=_send, args=(cfg, alert, event, logger), name="alert-email", daemon=True
    ).start()


def _send(cfg: Phase6Config, alert, event, logger) -> None:
    try:
        who = alert.employee_name or alert.identity_status
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(event.ts))
        msg = EmailMessage()
        msg["Subject"] = f"[Reality Connector] {alert.priority.upper()} alert - {who} on {alert.camera_id}"
        msg["From"] = cfg.smtp_from
        msg["To"] = ", ".join(cfg.alert_email_to)
        msg.set_content(
            f"Rule:       {alert.rule_name}\n"
            f"Priority:   {alert.priority}\n"
            f"Camera:     {alert.camera_id}\n"
            f"Identity:   {who}\n"
            f"Confidence: {event.confidence}\n"
            f"Time:       {stamp}\n"
            f"Track:      {event.track_id}\n"
            f"Event:      #{event.id} ({event.event_uid})\n"
        )
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=10) as s:
            if cfg.smtp_use_tls:
                s.starttls()
            if cfg.smtp_user:
                s.login(cfg.smtp_user, cfg.smtp_password or "")
            s.send_message(msg)
        logger.info("Alert email sent for alert #%s to %s", alert.id, cfg.alert_email_to)
    except Exception as e:  # noqa: BLE001 - never let mail failures propagate
        logger.warning("Alert email failed for alert #%s: %s", getattr(alert, "id", "?"), e)
