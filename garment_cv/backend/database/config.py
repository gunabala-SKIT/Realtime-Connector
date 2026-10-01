"""Where the Phase 6 database lives (spec section 19: "Create a structured database"). Just one
setting, the same shape as `zones/config.py` - the database's own schema lives in `db.py`."""
from __future__ import annotations

from pathlib import Path

from ..config import BASE_DIR, _get, ensure_env_loaded


def db_file_path() -> Path:
    ensure_env_loaded()
    raw = _get("GARMENT_DB_FILE", "configs/garment.db") or "configs/garment.db"
    p = Path(raw)
    return p if p.is_absolute() else BASE_DIR / p
