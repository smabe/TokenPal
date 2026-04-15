"""Read-only memory.db lookups for a whitelisted set of metrics."""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.registry import register_action
from tokenpal.brain.personality import contains_sensitive_term

_ALLOWED_METRICS = ("time_in_app", "switches_per_hour", "streaks", "session_count_today")


def _default_db_path(config: dict[str, Any]) -> Path:
    data_dir = config.get("data_dir") or "~/.tokenpal"
    return Path(data_dir).expanduser() / "memory.db"


def _connect_ro(path: Path) -> sqlite3.Connection:
    uri = f"file:{path}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _time_in_app(conn: sqlite3.Connection) -> str:
    cutoff = time.time() - 7 * 86400
    rows = conn.execute(
        "SELECT summary, COUNT(*) as cnt FROM observations "
        "WHERE event_type = 'app_switch' AND timestamp >= ? "
        "GROUP BY summary ORDER BY cnt DESC LIMIT 10",
        (cutoff,),
    ).fetchall()
    kept = [(app, cnt) for app, cnt in rows if not contains_sensitive_term(app)]
    if not kept:
        return "No app usage recorded in the last 7 days."
    lines = [f"{app}: {cnt} visits (7d)" for app, cnt in kept]
    return "\n".join(lines)


def _switches_per_hour(conn: sqlite3.Connection) -> str:
    cutoff = time.time() - 86400
    row = conn.execute(
        "SELECT COUNT(*) FROM observations "
        "WHERE event_type = 'app_switch' AND timestamp >= ?",
        (cutoff,),
    ).fetchone()
    count = row[0] if row else 0
    rate = count / 24.0
    return f"{count} app switches in the last 24h ({rate:.1f}/hour)."


def _streaks(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        "SELECT DISTINCT date(timestamp, 'unixepoch', 'localtime') FROM observations "
        "ORDER BY 1 DESC LIMIT 60"
    ).fetchall()
    if not rows:
        return "No session history."
    today = datetime.now().date()
    streak = 0
    for i, (d,) in enumerate(rows):
        dt = datetime.strptime(d, "%Y-%m-%d").date()
        if dt == today - timedelta(days=i):
            streak += 1
        else:
            break
    return f"Current daily-session streak: {streak} day(s)."


def _session_count_today(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT COUNT(DISTINCT session_id) FROM observations "
        "WHERE date(timestamp, 'unixepoch', 'localtime') = date('now', 'localtime')"
    ).fetchone()
    count = row[0] if row else 0
    return f"{count} session(s) today."


_DISPATCH = {
    "time_in_app": _time_in_app,
    "switches_per_hour": _switches_per_hour,
    "streaks": _streaks,
    "session_count_today": _session_count_today,
}


@register_action
class MemoryQueryAction(AbstractAction):
    action_name = "memory_query"
    description = (
        "Query local session history for one of: time_in_app, switches_per_hour, "
        "streaks, session_count_today."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "metric": {
                "type": "string",
                "description": (
                    "One of: time_in_app, switches_per_hour, streaks, session_count_today."
                ),
                "enum": list(_ALLOWED_METRICS),
            },
        },
        "required": ["metric"],
    }
    safe = True
    requires_confirm = False

    async def execute(self, **kwargs: Any) -> ActionResult:
        metric = kwargs.get("metric", "")
        if metric not in _DISPATCH:
            return ActionResult(
                output=f"Unknown metric. Allowed: {', '.join(_ALLOWED_METRICS)}.",
                success=False,
            )

        db_path = _default_db_path(self._config)
        if not db_path.exists():
            return ActionResult(output="No memory.db yet.", success=False)

        try:
            conn = _connect_ro(db_path)
        except sqlite3.Error as e:
            return ActionResult(output=f"Failed to open memory.db: {e}", success=False)

        try:
            output = _DISPATCH[metric](conn)
        except sqlite3.Error as e:
            return ActionResult(output=f"Query failed: {e}", success=False)
        finally:
            conn.close()

        return ActionResult(output=output)
