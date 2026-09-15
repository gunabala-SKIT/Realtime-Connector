"""Phase 6 configuration - reuses Phase 1-4 settings, adds SMTP for email alerts."""
from __future__ import annotations

import os
from dataclasses import dataclass

from phase4_events.config import Phase4Config


def _get(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    return v if v not in (None, "") else default


def _get_int(name: str, default: int) -> int:
    try:
        return int(_get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _get_bool(name: str, default: bool) -> bool:
    v = _get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Phase6Config:
    phase4: Phase4Config
    smtp_host: str | None
    smtp_port: int
    smtp_user: str | None
    smtp_password: str | None
    smtp_from: str
    smtp_use_tls: bool
    alert_email_to: list[str]

    @classmethod
    def load(cls, env_file: str | os.PathLike | None = None) -> "Phase6Config":
        phase4 = Phase4Config.load(env_file)  # runs load_dotenv()
        to_raw = _get("ALERT_EMAIL_TO", "") or ""
        return cls(
            phase4=phase4,
            smtp_host=_get("SMTP_HOST"),
            smtp_port=_get_int("SMTP_PORT", 587),
            smtp_user=_get("SMTP_USER"),
            smtp_password=_get("SMTP_PASSWORD"),
            smtp_from=_get("SMTP_FROM", _get("SMTP_USER", "reality-connector@localhost")),
            smtp_use_tls=_get_bool("SMTP_USE_TLS", True),
            alert_email_to=[a.strip() for a in to_raw.split(",") if a.strip()],
        )

    @property
    def email_configured(self) -> bool:
        return bool(self.smtp_host and self.alert_email_to)
