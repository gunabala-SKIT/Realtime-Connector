"""Logging setup with credential redaction.

Every record passes through `RedactFilter`, which strips passwords out of any
`rtsp://user:pass@host` string before it reaches the console or the log file, so a copied log
or screenshot never leaks a camera credential.
"""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from .cameras.config import CRED_RE

_FMT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = CRED_RE.sub(r"\1***\2", record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(CRED_RE.sub(r"\1***\2", a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: (CRED_RE.sub(r"\1***\2", v) if isinstance(v, str) else v)
                           for k, v in record.args.items()}
        return True


def get_logger(name: str = "garment_cv", level: str = "INFO", log_dir: str | Path = "logs") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    formatter = logging.Formatter(_FMT)
    redact = RedactFilter()

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(redact)
    logger.addHandler(console)

    path = Path(log_dir)
    path.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        path / "garment_cv.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.addFilter(redact)
    logger.addHandler(file_handler)

    return logger
