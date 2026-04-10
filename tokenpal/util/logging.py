"""Logging setup for TokenPal."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_DEFAULT_LOG_DIR = Path.home() / ".tokenpal" / "logs"


def setup_logging(
    level: int = logging.INFO,
    verbose: bool = False,
    log_dir: Path | None = None,
) -> None:
    """Configure structured logging to a file.

    Stderr is only used as a fallback if the log file can't be created,
    or when verbose=True for pre-overlay terminal output.
    """
    log_path = (log_dir or _DEFAULT_LOG_DIR)
    log_file = log_path / "tokenpal.log"

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    root = logging.getLogger("tokenpal")
    root.setLevel(logging.DEBUG)

    try:
        log_path.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        file_handler.setLevel(logging.DEBUG)
        root.addHandler(file_handler)
    except OSError:
        # Fall back to stderr only if file logging fails
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(fmt)
        stderr_handler.setLevel(level)
        root.addHandler(stderr_handler)
        root.warning("Could not create log file at %s — using stderr", log_file)
        return  # Already on stderr, don't add another

    if verbose:
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(fmt)
        stderr_handler.setLevel(logging.DEBUG)
        root.addHandler(stderr_handler)
