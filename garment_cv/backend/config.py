"""Process-level settings for the garment CV backend (host/port/logging).

Everything is read from environment variables, with `garment_cv/configs/.env` loaded first as
defaults (a real environment variable always wins - see `_load_env_file`). Camera-specific
settings live in `backend/cameras/config.py`; this module only holds what the process itself
needs to start up.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # garment_cv/


def _load_env_file(path: Path) -> None:
    """Tiny .env reader (KEY=VALUE, # comments, quoted values). Dependency-free by design -
    every env-driven product in this repo does this the same way, see manufacturing_intel/config.py."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        os.environ.setdefault(k, v)


_ENV_LOADED = False


def ensure_env_loaded() -> None:
    """Idempotent: safe to call from every config module without double-reading the file."""
    global _ENV_LOADED
    if not _ENV_LOADED:
        _load_env_file(BASE_DIR / "configs" / ".env")
        _ENV_LOADED = True


def _get(name: str, default: str | None = None) -> str | None:
    ensure_env_loaded()
    v = os.getenv(name)
    return v if v not in (None, "") else default


def _get_int(name: str, default: int) -> int:
    try:
        return int(_get(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass
class AppSettings:
    host: str
    port: int
    log_level: str
    log_dir: Path

    @classmethod
    def load(cls) -> "AppSettings":
        log_dir = Path(_get("GARMENT_LOG_DIR", "logs") or "logs")
        if not log_dir.is_absolute():
            log_dir = BASE_DIR / log_dir
        return cls(
            host=_get("GARMENT_HOST", "127.0.0.1") or "127.0.0.1",
            port=_get_int("GARMENT_PORT", 8200),
            log_level=(_get("GARMENT_LOG_LEVEL", "INFO") or "INFO").upper(),
            log_dir=log_dir,
        )
