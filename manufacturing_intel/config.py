"""Settings for the Manufacturing Process Intelligence Platform.

Read from environment variables (and an optional `manufacturing_intel/.env`). Deliberately
separate from Reality Connector's `phase1_camera/.env` - this is a different product.

  MPI_DB            database file                       (default manufacturing_intel/data/mpi.db)
  MPI_HOST          address to listen on                (default 127.0.0.1 - local only)
  MPI_PORT          port                                (default 8100)
  MPI_API_KEY       if set, the ingest endpoints (/api/ingest/*) require the header X-API-Key
  MPI_ADMIN_REMOTE  'true' lets non-local clients change configuration (default false)
  MPI_ALERT_INTERVAL_S   how often alerts are re-evaluated  (default 30)
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE = Path(__file__).resolve().parent


def _load_env_file(path: Path) -> None:
    """Tiny .env reader (KEY=VALUE, # comments); real environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


@dataclass
class Settings:
    db_path: str
    host: str
    port: int
    api_key: str | None
    admin_remote: bool
    alert_interval_s: float

    @classmethod
    def load(cls) -> "Settings":
        _load_env_file(BASE / ".env")
        db = os.getenv("MPI_DB") or str(BASE / "data" / "mpi.db")
        try:
            port = int(os.getenv("MPI_PORT", "8100"))
        except ValueError:
            port = 8100
        try:
            interval = max(5.0, float(os.getenv("MPI_ALERT_INTERVAL_S", "30")))
        except ValueError:
            interval = 30.0
        return cls(db_path=db, host=os.getenv("MPI_HOST", "127.0.0.1"), port=port,
                   api_key=os.getenv("MPI_API_KEY") or None,
                   admin_remote=os.getenv("MPI_ADMIN_REMOTE", "").lower() in ("1", "true", "yes"),
                   alert_interval_s=interval)
