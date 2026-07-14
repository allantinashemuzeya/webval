"""Structured logging: rich console output + plain-text audit file per run."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from rich.logging import RichHandler

_FORMAT_FILE = "%(asctime)sZ | %(levelname)-8s | %(name)s | %(message)s"
_CONFIGURED = False


def setup_logging(log_file: Path | None = None, level: str = "INFO") -> None:
    """Configure root logging once per process.

    The file handler (when a run directory exists) is the auditable execution
    log; the console handler is operator feedback.
    """
    global _CONFIGURED
    root = logging.getLogger("webval")
    root.setLevel(level.upper())
    if not _CONFIGURED:
        console = RichHandler(rich_tracebacks=True, show_path=False, markup=False)
        console.setLevel(level.upper())
        root.addHandler(console)
        _CONFIGURED = True
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        formatter = logging.Formatter(_FORMAT_FILE)
        formatter.converter = time.gmtime  # audit log in UTC
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.DEBUG)
        root.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"webval.{name}")
