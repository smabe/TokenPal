"""Shared chat-log formatting helpers.

Kept toolkit-agnostic so Textual, Qt, and any future overlay can render
timestamps the same way without dragging in Rich / Qt dependencies.
"""

from __future__ import annotations

from datetime import datetime


def format_chat_ts(ts: float, today_ymd: str | None = None) -> str:
    """Today → ``HH:MM AM/PM``; older → ``Mon DD HH:MM AM/PM``.

    ``today_ymd`` is an optional pre-computed ``%Y%m%d`` key so batch
    callers (history hydration, trim passes) don't pay for
    ``datetime.now()`` per row.
    """
    dt = datetime.fromtimestamp(ts)
    key = today_ymd or datetime.now().strftime("%Y%m%d")
    if dt.strftime("%Y%m%d") == key:
        return dt.strftime("%I:%M %p")
    return dt.strftime("%b %d %I:%M %p")
