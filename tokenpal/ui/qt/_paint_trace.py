"""Env-gated paint tracer for the qt-it audit.

Set ``TOKENPAL_PAINT_TRACE=1`` and each Qt window that's a candidate
for the rotating-shadow bug appends one line per ``paintEvent`` to
``<data_dir>/logs/paint_trace.log``:

    <t_mono> <window>     theta=<rad> pos=(<x>,<y>)

Diff per-paint state across windows: if the buddy paints at θ=0.40 rad
and the bubble paints 4 ms later at θ=0.43 rad, they're landing on
different DWM frames and the bubble is rotating relative to him.
Compare-by-timestamp is the falsifiable test the plan calls for.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import IO

_ENABLED = bool(os.environ.get("TOKENPAL_PAINT_TRACE"))
_log_fp: IO[str] | None = None


def enabled() -> bool:
    return _ENABLED


def _log() -> IO[str] | None:
    global _log_fp
    if not _ENABLED:
        return None
    if _log_fp is not None:
        return _log_fp
    base = Path(
        os.environ.get("TOKENPAL_DATA_DIR") or (Path.home() / ".tokenpal"),
    ).expanduser()
    logs = base / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    _log_fp = open(  # noqa: SIM115 -- lifetime = process
        logs / "paint_trace.log", "w", buffering=1, encoding="utf-8",
    )
    _log_fp.write(f"# session_start t={time.monotonic():.6f}\n")
    return _log_fp


def log_paint(
    window: str,
    *,
    theta: float | None = None,
    pos: tuple[float, float] | None = None,
) -> None:
    fp = _log()
    if fp is None:
        return
    parts = [f"{time.monotonic():.6f}", f"{window:<14}"]
    if theta is not None:
        parts.append(f"theta={theta:+.4f}")
    if pos is not None:
        parts.append(f"pos=({pos[0]:8.2f},{pos[1]:8.2f})")
    fp.write(" ".join(parts) + "\n")
