"""SQLite-backed session memory for cross-session persistence."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import stat
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    sense_name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    data_json TEXT,
    session_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_summaries (
    date TEXT PRIMARY KEY,
    summary TEXT NOT NULL,
    top_apps TEXT,
    total_active_minutes INTEGER,
    total_idle_minutes INTEGER
);
CREATE INDEX IF NOT EXISTS idx_obs_time ON observations(timestamp);
CREATE INDEX IF NOT EXISTS idx_obs_session ON observations(session_id);
"""


class MemoryStore:
    """Persists observations to SQLite for cross-session memory."""

    def __init__(
        self,
        db_path: Path,
        retention_days: int = 30,
        enabled: bool = True,
    ) -> None:
        self._db_path = db_path
        self._retention_days = retention_days
        self._enabled = enabled
        self._session_id = uuid.uuid4().hex[:8]
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def session_id(self) -> str:
        return self._session_id

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """Create the database, set permissions, run schema migration."""
        if not self._enabled:
            return

        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        # Create file with restricted permissions (0o600) before SQLite opens it
        if not self._db_path.exists():
            fd = os.open(str(self._db_path), os.O_CREAT | os.O_WRONLY, 0o600)
            os.close(fd)
        else:
            # Ensure existing file has correct permissions
            try:
                os.chmod(str(self._db_path), stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass  # Windows doesn't support Unix permissions

        self._conn = self._connect()
        with self._lock:
            self._conn.executescript(_SCHEMA)

        self._prune()
        log.info("MemoryStore ready — session %s, db at %s", self._session_id, self._db_path)

    def teardown(self) -> None:
        """Record session end and close the database."""
        if not self._enabled or not self._conn:
            return
        self.record_observation("system", "session_end", "Session ended")
        with self._lock:
            self._conn.close()
            self._conn = None
        log.info("MemoryStore closed")

    def _connect(self) -> sqlite3.Connection:
        """Connection factory — single point to swap in SQLCipher later."""
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_observation(
        self,
        sense_name: str,
        event_type: str,
        summary: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Insert an observation into the database."""
        if not self._enabled or not self._conn:
            return
        data_json = json.dumps(data) if data else None
        with self._lock:
            self._conn.execute(
                "INSERT INTO observations (timestamp, sense_name, event_type, summary, data_json, session_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (time.time(), sense_name, event_type, summary, data_json, self._session_id),
            )
            self._conn.commit()

    def record_session_start(self) -> None:
        self.record_observation("system", "session_start", "Session started")

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def get_history_lines(self, max_lines: int = 10) -> list[str]:
        """Build concise history lines for prompt injection.

        Returns one-liners like:
        - "Chrome: 47 visits across 12 sessions"
        - "5 sessions totaling ~8 hours"
        - "Last session: 2 hours ago, mostly VS Code"
        """
        if not self._enabled or not self._conn:
            return []

        lines: list[str] = []

        with self._lock:
            # Top apps by visit count (all time within retention)
            rows = self._conn.execute(
                "SELECT summary, COUNT(*) as cnt, COUNT(DISTINCT session_id) as sessions "
                "FROM observations WHERE event_type = 'app_switch' "
                "GROUP BY summary ORDER BY cnt DESC LIMIT 5"
            ).fetchall()
            for app_name, count, sessions in rows:
                if sessions > 1:
                    lines.append(f"{app_name}: {count} visits across {sessions} sessions")
                else:
                    lines.append(f"{app_name}: {count} visits this session")

            # Session count and total time
            session_rows = self._conn.execute(
                "SELECT session_id, MIN(timestamp), MAX(timestamp) "
                "FROM observations GROUP BY session_id"
            ).fetchall()
            if len(session_rows) > 1:
                total_hours = sum(
                    (end - start) / 3600 for _, start, end in session_rows if end > start
                )
                lines.append(f"{len(session_rows)} sessions totaling ~{total_hours:.1f} hours")

            # Last session summary (skip current session)
            prev_sessions = [
                r for r in session_rows if r[0] != self._session_id
            ]
            if prev_sessions:
                last = max(prev_sessions, key=lambda r: r[2])
                last_id, last_start, last_end = last
                ago_hours = (time.time() - last_end) / 3600
                duration_min = (last_end - last_start) / 60

                # Find top app in last session
                top_app_row = self._conn.execute(
                    "SELECT summary, COUNT(*) as cnt FROM observations "
                    "WHERE session_id = ? AND event_type = 'app_switch' "
                    "GROUP BY summary ORDER BY cnt DESC LIMIT 1",
                    (last_id,),
                ).fetchone()

                if ago_hours < 1:
                    ago_str = f"{int(ago_hours * 60)}m ago"
                elif ago_hours < 24:
                    ago_str = f"{ago_hours:.1f}h ago"
                else:
                    ago_str = f"{ago_hours / 24:.0f}d ago"

                if top_app_row:
                    lines.append(
                        f"Last session: {ago_str}, {int(duration_min)}m, mostly {top_app_row[0]}"
                    )
                else:
                    lines.append(f"Last session: {ago_str}, {int(duration_min)}m")

        return lines[:max_lines]

    def get_total_app_visits(self, app_name: str) -> int:
        """Get total visit count for an app across all sessions."""
        if not self._enabled or not self._conn:
            return 0
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM observations "
                "WHERE event_type = 'app_switch' AND summary = ?",
                (app_name,),
            ).fetchone()
            return row[0] if row else 0

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def _prune(self) -> None:
        """Delete observations older than retention period."""
        if not self._enabled or not self._conn:
            return
        cutoff = time.time() - self._retention_days * 86400
        with self._lock:
            result = self._conn.execute(
                "DELETE FROM observations WHERE timestamp < ?", (cutoff,)
            )
            self._conn.commit()
            if result.rowcount > 0:
                log.info("Pruned %d old observations", result.rowcount)
