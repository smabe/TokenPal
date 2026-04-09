"""Logging setup for TokenPal."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_LOG_DIR = Path.home() / ".tokenpal" / "logs"
_LOG_FILE = _LOG_DIR / "tokenpal.log"


def setup_logging(level: int = logging.INFO) -> None:
    """Configure structured logging to stderr and a file."""
    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(fmt)

    root = logging.getLogger("tokenpal")
    root.setLevel(level)
    root.addHandler(stderr_handler)

    # File handler — tail with: tail -f ~/.tokenpal/logs/tokenpal.log
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(_LOG_FILE, encoding="utf-8")
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError:
        root.warning("Could not create log file at %s", _LOG_FILE)
