"""Where zones are persisted (Phase 4). Just one setting - the rest of "zones config" is the
zones themselves, which live in that file via `ZoneStore`, not in environment variables."""
from __future__ import annotations

from pathlib import Path

from ..config import BASE_DIR, _get, ensure_env_loaded


def zones_file_path() -> Path:
    ensure_env_loaded()
    raw = _get("GARMENT_ZONES_FILE", "configs/zones.json") or "configs/zones.json"
    p = Path(raw)
    return p if p.is_absolute() else BASE_DIR / p
