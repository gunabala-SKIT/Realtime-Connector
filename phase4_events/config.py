"""Phase 4 configuration - reuses Phase 1/2/3 settings, adds event + API params."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from phase3_identity.config import Phase3Config

BASE_DIR = Path(__file__).resolve().parent


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


def _resolve(p: str, base: Path = BASE_DIR) -> Path:
    path = Path(p)
    return path if path.is_absolute() else base / path


@dataclass
class Phase4Config:
    phase3: Phase3Config
    camera_id: str
    events_db: Path
    snapshot_dir: Path
    emit_unverified: bool
    dedup_track_ttl_s: float
    api_host: str
    api_port: int

    @classmethod
    def load(cls, env_file: str | os.PathLike | None = None) -> "Phase4Config":
        phase3 = Phase3Config.load(env_file)  # runs load_dotenv()
        return cls(
            phase3=phase3,
            camera_id=_get("CAMERA_ID", "cam-1") or "cam-1",
            events_db=_resolve(_get("EVENTS_DB", "events.db")),
            snapshot_dir=_resolve(_get("SNAPSHOT_DIR", "snapshots")),
            emit_unverified=_get_bool("EMIT_UNVERIFIED_EVENTS", False),
            dedup_track_ttl_s=float(_get("DEDUP_TRACK_TTL_S", "8") or 8),
            api_host=_get("API_HOST", "127.0.0.1") or "127.0.0.1",
            api_port=_get_int("API_PORT", 8000),
        )
