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
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta
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
CREATE TABLE IF NOT EXISTS hydration_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    amount_oz REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hydration_time ON hydration_log(timestamp);
CREATE TABLE IF NOT EXISTS habit_log (
    name TEXT NOT NULL,
    date TEXT NOT NULL,
    PRIMARY KEY (name, date)
);
CREATE INDEX IF NOT EXISTS idx_habit_name ON habit_log(name);
CREATE TABLE IF NOT EXISTS mood_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    mood TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mood_time ON mood_log(timestamp);
CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    tool_name TEXT NOT NULL,
    duration_ms REAL NOT NULL,
    success INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_name ON tool_calls(tool_name);
CREATE INDEX IF NOT EXISTS idx_tool_calls_time ON tool_calls(timestamp);
CREATE TABLE IF NOT EXISTS research_cache (
    question_hash TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    sources_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS app_enrichment (
    app_name TEXT PRIMARY KEY,
    description TEXT,
    fetched_at REAL NOT NULL,
    success INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_throughput_estimators (
    server_url TEXT NOT NULL,
    model TEXT NOT NULL,
    decode_tps REAL NOT NULL,
    ttft_s REAL NOT NULL,
    sample_count INTEGER NOT NULL,
    updated_at REAL NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (server_url, model)
);
"""

# Bump when the estimator math changes in a way that invalidates prior
# decode_tps / ttft_s readings. Rows with a lower version are ignored.
LLM_ESTIMATOR_SCHEMA_VERSION = 1


# PRAGMA user_version based migrations. _SCHEMA above stays frozen as the
# legacy "always idempotent" base; new tables land via migrations. Each entry
# in _MIGRATIONS takes the db from version (index) to version (index + 1).
# Bump CURRENT_SCHEMA_VERSION and append a migration when adding a new table
# or altering an existing one.


def _migration_1_session_summaries(conn: sqlite3.Connection) -> None:
    """v0 -> v1: add session_summaries for the session-handoff feature."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS session_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            session_id TEXT NOT NULL,
            window_start REAL NOT NULL,
            window_end REAL NOT NULL,
            summary TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_session_summaries_time
            ON session_summaries(timestamp);
        """
    )


def _migration_2_active_intent(conn: sqlite3.Connection) -> None:
    """v1 -> v2: add active_intent (single-row) for intent-tracking feature.

    The CHECK (id = 1) guarantees at most one intent row ever exists; writes
    use INSERT OR REPLACE so /intent <text> always overwrites the prior.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS active_intent (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            text TEXT NOT NULL,
            started_at REAL NOT NULL,
            session_id TEXT NOT NULL
        );
        """
    )


def _migration_3_chat_log(conn: sqlite3.Connection) -> None:
    """v2 -> v3: add chat_log for transcript persistence across restarts."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS chat_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            speaker TEXT NOT NULL,
            text TEXT NOT NULL,
            url TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_chat_log_time ON chat_log(timestamp);
        """
    )


_MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [
    _migration_1_session_summaries,
    _migration_2_active_intent,
    _migration_3_chat_log,
]

CURRENT_SCHEMA_VERSION = len(_MIGRATIONS)


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
        self._callback_cache: list[str] | None = None
        # chat_log cap + in-memory row count. Loaded from SQLite on setup so
        # per-insert writes don't pay a SELECT COUNT every call.
        self._chat_log_max_persisted: int = 200
        self._chat_log_count: int = 0

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
            self._apply_migrations(self._conn)

        self._prune()
        self.aggregate_daily_summaries()
        # Prime the in-memory chat_log counter once so record_chat_entry
        # doesn't run SELECT COUNT(*) on every insert.
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM chat_log"
            ).fetchone()
        self._chat_log_count = int(row[0]) if row else 0
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

    @staticmethod
    def _apply_migrations(conn: sqlite3.Connection) -> None:
        """Run any pending migrations against conn, bumping user_version."""
        row = conn.execute("PRAGMA user_version").fetchone()
        current = int(row[0]) if row else 0
        if current >= CURRENT_SCHEMA_VERSION:
            return
        for idx in range(current, CURRENT_SCHEMA_VERSION):
            migration = _MIGRATIONS[idx]
            log.info(
                "Applying memory.db migration %d -> %d (%s)",
                idx,
                idx + 1,
                migration.__name__,
            )
            migration(conn)
            conn.execute(f"PRAGMA user_version = {idx + 1}")
        conn.commit()

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
    # Chat log — UI-transcript persistence for cross-restart recall
    # ------------------------------------------------------------------

    def set_chat_log_max_persisted(self, n: int) -> None:
        """Update the in-memory cap used by ``record_chat_entry``. Callers
        pass 0 to disable persistence without dropping existing rows."""
        self._chat_log_max_persisted = max(0, int(n))

    def record_chat_entry(
        self,
        speaker: str,
        text: str,
        url: str | None = None,
    ) -> None:
        """Insert one chat-log row. Trims when the in-memory count exceeds
        ``max_persisted * 1.5`` so DELETE doesn't run on every insert.
        Parameterized — no SQL injection even for arbitrary speaker/text/url.
        """
        if not self._enabled or not self._conn:
            return
        cap = self._chat_log_max_persisted
        if cap <= 0:
            return
        with self._lock:
            self._conn.execute(
                "INSERT INTO chat_log (timestamp, speaker, text, url) "
                "VALUES (?, ?, ?, ?)",
                (time.time(), speaker, text, url),
            )
            self._chat_log_count += 1
            if self._chat_log_count > int(cap * 1.5):
                excess = self._chat_log_count - cap
                self._conn.execute(
                    "DELETE FROM chat_log WHERE id IN ("
                    "SELECT id FROM chat_log ORDER BY id ASC LIMIT ?"
                    ")",
                    (excess,),
                )
                self._chat_log_count = cap
            self._conn.commit()

    def get_recent_chat_entries(
        self, limit: int
    ) -> list[tuple[float, str, str, str | None]]:
        """Return up to *limit* most recent chat-log rows in chronological
        order (oldest first) as (timestamp, speaker, text, url) tuples.
        """
        if not self._enabled or not self._conn or limit <= 0:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT timestamp, speaker, text, url FROM chat_log "
                "ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [
            (float(r[0]), str(r[1]), str(r[2]), (str(r[3]) if r[3] is not None else None))
            for r in reversed(rows)
        ]

    def clear_chat_log(self) -> None:
        """Wipe every row in chat_log."""
        if not self._enabled or not self._conn:
            return
        with self._lock:
            self._conn.execute("DELETE FROM chat_log")
            self._conn.commit()
        self._chat_log_count = 0

    # ------------------------------------------------------------------
    # Session summaries — see plans/buddy-utility-wedges.md
    # ------------------------------------------------------------------

    def record_summary(
        self, summary: str, window_start: float, window_end: float
    ) -> None:
        """Insert a periodic session summary row."""
        if not self._enabled or not self._conn:
            return
        with self._lock:
            self._conn.execute(
                "INSERT INTO session_summaries "
                "(timestamp, session_id, window_start, window_end, summary) "
                "VALUES (?, ?, ?, ?, ?)",
                (time.time(), self._session_id, window_start, window_end, summary),
            )
            self._conn.commit()

    def get_latest_summary(
        self, max_lookback_s: float
    ) -> tuple[float, str] | None:
        """Return (timestamp, summary) of the most recent summary within
        max_lookback_s seconds, or None.
        """
        if not self._enabled or not self._conn:
            return None
        cutoff = time.time() - max_lookback_s
        with self._lock:
            row = self._conn.execute(
                "SELECT timestamp, summary FROM session_summaries "
                "WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT 1",
                (cutoff,),
            ).fetchone()
        if row is None:
            return None
        return float(row[0]), str(row[1])

    def get_recent_summaries(
        self, since_ts: float, limit: int = 5
    ) -> list[tuple[float, str]]:
        """Return [(timestamp, summary)] newer than since_ts, newest first."""
        if not self._enabled or not self._conn:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT timestamp, summary FROM session_summaries "
                "WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT ?",
                (since_ts, limit),
            ).fetchall()
        return [(float(r[0]), str(r[1])) for r in rows]

    def count_observations_in_window(
        self, window_start: float, window_end: float
    ) -> int:
        """How many observations landed in [window_start, window_end)?

        Used as the skip-if-idle guard for the session summarizer — zero
        means the window was pure-idle and shouldn't be summarized.
        """
        if not self._enabled or not self._conn:
            return 0
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM observations "
                "WHERE timestamp >= ? AND timestamp < ?",
                (window_start, window_end),
            ).fetchone()
        return int(row[0]) if row else 0

    def get_window_digest(
        self,
        window_start: float,
        window_end: float,
        max_apps: int = 5,
        max_events: int = 20,
    ) -> dict[str, Any]:
        """Compact representation of activity in [window_start, window_end).

        Returns a dict with top apps by visit count, per-sense event counts,
        and up to max_events individual event summaries ordered by time.
        Fed to the session summarizer's LLM prompt — keeps the prompt size
        bounded regardless of how busy the window was.
        """
        if not self._enabled or not self._conn:
            return {"apps": [], "sense_counts": {}, "events": []}
        with self._lock:
            app_rows = self._conn.execute(
                "SELECT summary, COUNT(*) FROM observations "
                "WHERE event_type = 'app_switch' "
                "AND timestamp >= ? AND timestamp < ? "
                "GROUP BY summary ORDER BY COUNT(*) DESC LIMIT ?",
                (window_start, window_end, max_apps),
            ).fetchall()
            sense_rows = self._conn.execute(
                "SELECT sense_name, COUNT(*) FROM observations "
                "WHERE timestamp >= ? AND timestamp < ? "
                "GROUP BY sense_name",
                (window_start, window_end),
            ).fetchall()
            event_rows = self._conn.execute(
                "SELECT timestamp, sense_name, event_type, summary FROM observations "
                "WHERE timestamp >= ? AND timestamp < ? "
                "ORDER BY timestamp ASC LIMIT ?",
                (window_start, window_end, max_events),
            ).fetchall()
        return {
            "apps": [(str(r[0]), int(r[1])) for r in app_rows],
            "sense_counts": {str(r[0]): int(r[1]) for r in sense_rows},
            "events": [
                (float(r[0]), str(r[1]), str(r[2]), str(r[3])) for r in event_rows
            ],
        }

    # ------------------------------------------------------------------
    # Active intent — see plans/buddy-utility-wedges.md
    # ------------------------------------------------------------------

    def set_active_intent(self, text: str) -> None:
        """Upsert the single active-intent row with the current session_id."""
        if not self._enabled or not self._conn:
            return
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO active_intent "
                "(id, text, started_at, session_id) VALUES (1, ?, ?, ?)",
                (text, time.time(), self._session_id),
            )
            self._conn.commit()

    def get_active_intent(self) -> tuple[str, float, str] | None:
        """Return (text, started_at, session_id) or None if no intent is set."""
        if not self._enabled or not self._conn:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT text, started_at, session_id FROM active_intent WHERE id = 1"
            ).fetchone()
        if row is None:
            return None
        return str(row[0]), float(row[1]), str(row[2])

    def clear_active_intent(self) -> None:
        """Remove the active-intent row, if any."""
        if not self._enabled or not self._conn:
            return
        with self._lock:
            self._conn.execute("DELETE FROM active_intent WHERE id = 1")
            self._conn.commit()

    # ------------------------------------------------------------------
    # End-of-day summary tracking — see plans/buddy-utility-wedges.md
    # ------------------------------------------------------------------

    def has_shown_eod(self, date_str: str) -> bool:
        """True when we've already fired an EOD summary bubble for date_str."""
        if not self._enabled or not self._conn:
            return False
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM observations "
                "WHERE sense_name = 'system' AND event_type = 'eod_shown' "
                "AND summary = ? LIMIT 1",
                (date_str,),
            ).fetchone()
        return row is not None

    def mark_eod_shown(self, date_str: str) -> None:
        """Record that we displayed the EOD bubble for date_str."""
        self.record_observation("system", "eod_shown", date_str)

    def get_day_digest(self, date_str: str) -> dict[str, Any]:
        """Per-date rollup for the EOD summarizer.

        Returns top apps by visit count, session count + total minutes,
        idle_return count, and the most recent session summary text (if any)
        landed during the date.  Empty apps list means nothing to report.
        """
        empty: dict[str, Any] = {
            "date": date_str,
            "apps": [],
            "session_count": 0,
            "active_minutes": 0,
            "idle_returns": 0,
            "last_summary": None,
        }
        if not self._enabled or not self._conn:
            return empty
        with self._lock:
            app_rows = self._conn.execute(
                "SELECT summary, COUNT(*) FROM observations "
                "WHERE event_type = 'app_switch' "
                "AND date(timestamp, 'unixepoch', 'localtime') = ? "
                "GROUP BY summary ORDER BY COUNT(*) DESC LIMIT 5",
                (date_str,),
            ).fetchall()
            session_rows = self._conn.execute(
                "SELECT session_id, MIN(timestamp), MAX(timestamp) "
                "FROM observations "
                "WHERE date(timestamp, 'unixepoch', 'localtime') = ? "
                "GROUP BY session_id",
                (date_str,),
            ).fetchall()
            idle_row = self._conn.execute(
                "SELECT COUNT(*) FROM observations "
                "WHERE event_type = 'idle_return' "
                "AND date(timestamp, 'unixepoch', 'localtime') = ?",
                (date_str,),
            ).fetchone()
            summary_row = self._conn.execute(
                "SELECT summary FROM session_summaries "
                "WHERE date(timestamp, 'unixepoch', 'localtime') = ? "
                "ORDER BY timestamp DESC LIMIT 1",
                (date_str,),
            ).fetchone()
        active_min = int(
            sum((end - start) / 60 for _, start, end in session_rows if end > start)
        )
        return {
            "date": date_str,
            "apps": [(str(r[0]), int(r[1])) for r in app_rows],
            "session_count": len(session_rows),
            "active_minutes": active_min,
            "idle_returns": int(idle_row[0]) if idle_row else 0,
            "last_summary": str(summary_row[0]) if summary_row else None,
        }

    # ------------------------------------------------------------------
    # LLM throughput estimator persistence — see plans/gpu-scaling.md
    # ------------------------------------------------------------------

    def get_llm_throughput_estimator(
        self, server_url: str, model: str
    ) -> tuple[float, float, int] | None:
        """Return (decode_tps, ttft_s, sample_count) if a row matches both the
        key and the current schema_version, else None.
        """
        if not self._enabled or not self._conn:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT decode_tps, ttft_s, sample_count FROM llm_throughput_estimators "
                "WHERE server_url = ? AND model = ? AND schema_version = ?",
                (server_url, model, LLM_ESTIMATOR_SCHEMA_VERSION),
            ).fetchone()
        if row is None:
            return None
        return float(row[0]), float(row[1]), int(row[2])

    def save_llm_throughput_estimator(
        self,
        server_url: str,
        model: str,
        decode_tps: float,
        ttft_s: float,
        sample_count: int,
    ) -> None:
        """Upsert the current EWMA state for (server_url, model)."""
        if not self._enabled or not self._conn:
            return
        with self._lock:
            self._conn.execute(
                "INSERT INTO llm_throughput_estimators "
                "(server_url, model, decode_tps, ttft_s, sample_count, updated_at, schema_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(server_url, model) DO UPDATE SET "
                "decode_tps = excluded.decode_tps, "
                "ttft_s = excluded.ttft_s, "
                "sample_count = excluded.sample_count, "
                "updated_at = excluded.updated_at, "
                "schema_version = excluded.schema_version",
                (
                    server_url, model, decode_tps, ttft_s, sample_count,
                    time.time(), LLM_ESTIMATOR_SCHEMA_VERSION,
                ),
            )
            self._conn.commit()

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

    # ------------------------------------------------------------------
    # Daily aggregation
    # ------------------------------------------------------------------

    def aggregate_daily_summaries(self) -> None:
        """Populate daily_summaries for all dates that are missing.

        Called on startup — backfills historical dates, then ensures
        yesterday is summarized.  Skips today (incomplete data).
        """
        if not self._enabled or not self._conn:
            return

        with self._lock:
            # Find all dates with observations (excluding today)
            today_str = datetime.now().strftime("%Y-%m-%d")
            rows = self._conn.execute(
                "SELECT DISTINCT date(timestamp, 'unixepoch', 'localtime') as d "
                "FROM observations WHERE d != ? ORDER BY d",
                (today_str,),
            ).fetchall()
            obs_dates = {r[0] for r in rows}

            # Find dates already summarized
            existing = self._conn.execute(
                "SELECT date FROM daily_summaries"
            ).fetchall()
            existing_dates = {r[0] for r in existing}

            missing = obs_dates - existing_dates
            if not missing:
                return

            log.info("Backfilling %d daily summaries", len(missing))
            for date_str in sorted(missing):
                self._aggregate_one_day(date_str)
            self._conn.commit()

    def _aggregate_one_day(self, date_str: str) -> None:
        """Summarize a single day's observations into daily_summaries.

        Must be called while holding self._lock.  Caller is responsible
        for committing the transaction.
        """
        assert self._conn is not None

        app_rows = self._conn.execute(
            "SELECT summary, COUNT(*) as cnt FROM observations "
            "WHERE event_type = 'app_switch' "
            "AND date(timestamp, 'unixepoch', 'localtime') = ? "
            "GROUP BY summary ORDER BY cnt DESC",
            (date_str,),
        ).fetchall()
        top_apps = json.dumps({name: cnt for name, cnt in app_rows[:10]})

        session_rows = self._conn.execute(
            "SELECT session_id, MIN(timestamp), MAX(timestamp) "
            "FROM observations "
            "WHERE date(timestamp, 'unixepoch', 'localtime') = ? "
            "GROUP BY session_id",
            (date_str,),
        ).fetchall()
        total_active_min = int(
            sum((end - start) / 60 for _, start, end in session_rows if end > start)
        )

        idle_rows = self._conn.execute(
            "SELECT COUNT(*) FROM observations "
            "WHERE event_type = 'idle_return' "
            "AND date(timestamp, 'unixepoch', 'localtime') = ?",
            (date_str,),
        ).fetchone()
        idle_count = idle_rows[0] if idle_rows else 0

        app_summary = ", ".join(f"{n}({c})" for n, c in app_rows[:5])
        summary = (
            f"{len(session_rows)} sessions, {total_active_min}m active, "
            f"{idle_count} idle returns. Top: {app_summary}"
        )

        self._conn.execute(
            "INSERT OR REPLACE INTO daily_summaries "
            "(date, summary, top_apps, total_active_minutes, total_idle_minutes) "
            "VALUES (?, ?, ?, ?, ?)",
            (date_str, summary, top_apps, total_active_min, idle_count),
        )

    # ------------------------------------------------------------------
    # Pattern detection
    # ------------------------------------------------------------------

    @staticmethod
    def _is_sensitive(app: str, exclude: set[str]) -> bool:
        """Check if an app name matches the sensitive app exclusion list."""
        app_lower = app.lower()
        return any(s in app_lower for s in exclude)

    def get_daily_streak_days(self) -> int:
        """How many consecutive days ending today have at least one
        observation recorded. Matches the `memory_query streaks` action
        but returns an int so idle-rule predicates can threshold on it.

        Returns 0 when DB is disabled, empty, or the most recent
        recorded date isn't today (the user hasn't opened the buddy yet).
        """
        if not self._enabled or not self._conn:
            return 0
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT date(timestamp, 'unixepoch', 'localtime') "
                "FROM observations ORDER BY 1 DESC LIMIT 60"
            ).fetchall()
        if not rows:
            return 0
        today = datetime.now().date()
        streak = 0
        for i, (d,) in enumerate(rows):
            dt = datetime.strptime(d, "%Y-%m-%d").date()
            if dt == today - timedelta(days=i):
                streak += 1
            else:
                break
        return streak

    def get_install_age_days(self) -> int:
        """Days between the oldest observation timestamp and today.

        Used by the anniversary idle rule to fire on 7/30/90/180/365 day
        milestones. Returns 0 when DB is disabled or empty — the rule's
        predicate then has nothing to match on.
        """
        if not self._enabled or not self._conn:
            return 0
        with self._lock:
            row = self._conn.execute(
                "SELECT MIN(timestamp) FROM observations"
            ).fetchone()
        if not row or row[0] is None:
            return 0
        oldest = datetime.fromtimestamp(float(row[0]))
        delta = datetime.now() - oldest
        return max(0, delta.days)

    def get_pattern_callbacks(
        self,
        max_callbacks: int = 3,
        sensitive_apps: list[str] | None = None,
    ) -> list[str]:
        """Detect behavioral patterns and return natural-language callbacks.

        Each callback is a factual observation — the LLM adds the humor.
        Results are cached for the session (patterns don't change mid-session).
        """
        if not self._enabled or not self._conn:
            return []

        if self._callback_cache is not None:
            return self._callback_cache

        exclude = set(sensitive_apps or [])
        callbacks: list[str] = []

        callbacks.extend(self._detect_day_of_week_patterns(exclude))
        callbacks.extend(self._detect_time_of_day_patterns(exclude))
        callbacks.extend(self._detect_streaks(exclude))
        callbacks.extend(self._detect_rituals(exclude))

        self._callback_cache = callbacks[:max_callbacks]
        log.debug("Pattern callbacks: %s", self._callback_cache)
        return self._callback_cache

    def _detect_day_of_week_patterns(self, exclude: set[str]) -> list[str]:
        """Find apps used disproportionately on a specific weekday.

        Looks for >=2x skew toward a single day with >=3 data points.
        """
        if not self._conn:
            return []

        with self._lock:
            rows = self._conn.execute(
                "SELECT summary, "
                "CAST(strftime('%w', timestamp, 'unixepoch', 'localtime') AS INTEGER) as dow, "
                "COUNT(*) as cnt "
                "FROM observations WHERE event_type = 'app_switch' "
                "GROUP BY summary, dow"
            ).fetchall()

        app_dow: dict[str, dict[int, int]] = {}
        for app, dow, cnt in rows:
            if self._is_sensitive(app, exclude):
                continue
            app_dow.setdefault(app, {})[dow] = cnt

        day_names = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
        callbacks: list[str] = []

        for app, dow_counts in app_dow.items():
            total = sum(dow_counts.values())
            if total < 6:
                continue
            for dow, cnt in dow_counts.items():
                expected = total / 7
                if cnt >= 3 and cnt >= expected * 2:
                    callbacks.append(
                        f"You use {app} on {day_names[dow]}s more than any other day "
                        f"({cnt} of your {total} visits)"
                    )

        return callbacks[:1]

    def _detect_time_of_day_patterns(self, exclude: set[str]) -> list[str]:
        """Find the app you consistently open first each session."""
        if not self._conn:
            return []

        with self._lock:
            rows = self._conn.execute(
                "SELECT o.session_id, o.summary, o.timestamp FROM observations o "
                "INNER JOIN ("
                "  SELECT session_id, MIN(timestamp) as first_ts "
                "  FROM observations WHERE event_type = 'app_switch' "
                "  GROUP BY session_id"
                ") f ON o.session_id = f.session_id AND o.timestamp = f.first_ts "
                "WHERE o.event_type = 'app_switch'"
            ).fetchall()

        if len(rows) < 3:
            return []

        first_apps = Counter(
            app for _, app, _ in rows
            if not self._is_sensitive(app, exclude)
        )
        if not first_apps:
            return []

        top_app, top_count = first_apps.most_common(1)[0]
        if top_count >= 3 and top_count / len(rows) >= 0.4:
            return [
                f"You open {top_app} first in {top_count} of your last "
                f"{len(rows)} sessions"
            ]
        return []

    def _detect_streaks(self, exclude: set[str]) -> list[str]:
        """Find apps used on consecutive days."""
        if not self._conn:
            return []

        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT summary, date(timestamp, 'unixepoch', 'localtime') as d "
                "FROM observations WHERE event_type = 'app_switch' "
                "ORDER BY summary, d"
            ).fetchall()

        app_dates: dict[str, list[datetime]] = {}
        for app, d in rows:
            if self._is_sensitive(app, exclude):
                continue
            app_dates.setdefault(app, []).append(
                datetime.strptime(d, "%Y-%m-%d")
            )

        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        best_callback = ""
        best_streak = 0

        for app, dates in app_dates.items():
            unique_dates = sorted(set(dates))
            streak = 1
            for i in range(len(unique_dates) - 1, 0, -1):
                if (unique_dates[i] - unique_dates[i - 1]).days == 1:
                    streak += 1
                else:
                    break

            last_date = unique_dates[-1]
            if streak >= 3 and (today - last_date).days <= 1 and streak > best_streak:
                best_streak = streak
                best_callback = f"{app} streak: {streak} consecutive days"

        return [best_callback] if best_callback else []

    def _detect_rituals(self, exclude: set[str]) -> list[str]:
        """Find recurring app sequences in the first few minutes of sessions."""
        if not self._conn:
            return []

        with self._lock:
            # Use window function to get only the first 3 app switches per session
            rows = self._conn.execute(
                "SELECT session_id, summary FROM ("
                "  SELECT session_id, summary, "
                "    ROW_NUMBER() OVER (PARTITION BY session_id ORDER BY timestamp) as rn "
                "  FROM observations WHERE event_type = 'app_switch'"
                ") WHERE rn <= 3"
            ).fetchall()

        sessions: dict[str, list[str]] = {}
        for sid, app in rows:
            if self._is_sensitive(app, exclude):
                continue
            sessions.setdefault(sid, []).append(app)

        sequences = [tuple(apps) for apps in sessions.values() if len(apps) >= 3]
        if len(sequences) < 3:
            return []

        seq_counts = Counter(sequences)
        top_seq, top_count = seq_counts.most_common(1)[0]
        if top_count >= 3:
            chain = " \u2192 ".join(top_seq)
            return [f"Your usual startup sequence: {chain} ({top_count} sessions)"]
        return []

    # ------------------------------------------------------------------
    # Phase 3: hydration / habit / mood logs (additive)
    # ------------------------------------------------------------------

    def log_hydration(self, amount_oz: float) -> None:
        """Append a hydration entry. Amount in fluid ounces."""
        if not self._enabled or not self._conn:
            return
        with self._lock:
            self._conn.execute(
                "INSERT INTO hydration_log (timestamp, amount_oz) VALUES (?, ?)",
                (time.time(), float(amount_oz)),
            )
            self._conn.commit()

    def get_hydration_today(self) -> float:
        """Return total hydration recorded so far today (local time), in oz."""
        if not self._enabled or not self._conn:
            return 0.0
        today_str = datetime.now().strftime("%Y-%m-%d")
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(amount_oz), 0) FROM hydration_log "
                "WHERE date(timestamp, 'unixepoch', 'localtime') = ?",
                (today_str,),
            ).fetchone()
        return float(row[0]) if row else 0.0

    def log_habit(self, name: str, date: str | None = None) -> None:
        """Mark habit *name* done for *date* (YYYY-MM-DD, defaults to today)."""
        if not self._enabled or not self._conn:
            return
        date_str = date or datetime.now().strftime("%Y-%m-%d")
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO habit_log (name, date) VALUES (?, ?)",
                (name, date_str),
            )
            self._conn.commit()

    def get_habit_streak(self, name: str) -> tuple[int, int]:
        """Return (current_streak, longest_streak) for habit *name* in days."""
        if not self._enabled or not self._conn:
            return (0, 0)
        with self._lock:
            rows = self._conn.execute(
                "SELECT date FROM habit_log WHERE name = ? ORDER BY date",
                (name,),
            ).fetchall()
        if not rows:
            return (0, 0)

        dates = sorted({datetime.strptime(r[0], "%Y-%m-%d").date() for r in rows})
        longest = 1
        run = 1
        for i in range(1, len(dates)):
            if (dates[i] - dates[i - 1]).days == 1:
                run += 1
                longest = max(longest, run)
            else:
                run = 1

        today = datetime.now().date()
        last = dates[-1]
        gap = (today - last).days
        if gap > 1:
            current = 0
        else:
            # Count back from `last` while consecutive.
            current = 1
            for i in range(len(dates) - 1, 0, -1):
                if (dates[i] - dates[i - 1]).days == 1:
                    current += 1
                else:
                    break
        return (current, longest)

    # ------------------------------------------------------------------
    # Tool usage stats + research cache
    # ------------------------------------------------------------------

    def record_tool_call(self, tool_name: str, duration_ms: float, success: bool) -> None:
        if not self._enabled or not self._conn:
            return
        with self._lock:
            self._conn.execute(
                "INSERT INTO tool_calls (timestamp, tool_name, duration_ms, success) "
                "VALUES (?, ?, ?, ?)",
                (time.time(), tool_name, float(duration_ms), 1 if success else 0),
            )
            self._conn.commit()

    def tool_usage_counts(self, since_days: int | None = None) -> dict[str, int]:
        """Return ``{tool_name: call_count}``. Filters failed calls out — a
        tool that crashed every time isn't "used", it's "attempted"."""
        if not self._enabled or not self._conn:
            return {}
        query = "SELECT tool_name, COUNT(*) FROM tool_calls WHERE success = 1"
        params: tuple[Any, ...] = ()
        if since_days is not None:
            query += " AND timestamp >= ?"
            params = (time.time() - since_days * 86400,)
        query += " GROUP BY tool_name"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return {name: cnt for name, cnt in rows}

    def cache_research_answer(
        self,
        question_hash: str,
        question: str,
        answer: str,
        sources_json: str,
    ) -> None:
        if not self._enabled or not self._conn:
            return
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO research_cache "
                "(question_hash, question, answer, sources_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (question_hash, question, answer, sources_json, time.time()),
            )
            self._conn.commit()

    @staticmethod
    def research_cache_key(question: str, mode: str = "") -> str:
        """Canonical cache key for research answers.

        Mode matters: a slash-invoked run in "search" mode produces
        different provenance than a local run for the same question.
        Keying on mode keeps result sets separate so /refine follows a
        consistent trust model per entry."""
        import hashlib
        prefix = f"{mode}:" if mode else ""
        raw = f"{prefix}{question.strip().lower()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get_research_answer(
        self, question_hash: str, max_age_s: float
    ) -> tuple[str, str, float] | None:
        """Return ``(answer, sources_json, age_s)`` if a fresh hit exists."""
        if not self._enabled or not self._conn:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT answer, sources_json, created_at FROM research_cache "
                "WHERE question_hash = ?",
                (question_hash,),
            ).fetchone()
        if row is None:
            return None
        answer, sources_json, created_at = row
        age = time.time() - created_at
        if age > max_age_s:
            return None
        return (answer, sources_json, age)

    def get_latest_research(
        self, max_age_s: float
    ) -> tuple[str, str, str, float] | None:
        """Return ``(question, answer, sources_json, age_s)`` for the most
        recent research within *max_age_s*, or None. Used by /refine to pull
        the most-recently-fetched source pool without needing the user to
        retype the original question."""
        if not self._enabled or not self._conn:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT question, answer, sources_json, created_at "
                "FROM research_cache ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        question, answer, sources_json, created_at = row
        age = time.time() - created_at
        if age > max_age_s:
            return None
        return (question, answer, sources_json, age)

    def get_app_enrichment(
        self,
        app_name: str,
        fresh_after_s: float,
        retry_after_s: float,
    ) -> tuple[str | None, bool] | None:
        """Return ``(description, still_fresh)`` for a cached app, or None.

        ``still_fresh`` is True when a successful row is younger than
        ``fresh_after_s`` or a failed row is younger than ``retry_after_s``
        — i.e. the caller should use (or respect) the cached value rather
        than re-fetching. When False, callers should treat this as a miss.
        """
        if not self._enabled or not self._conn:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT description, fetched_at, success FROM app_enrichment "
                "WHERE app_name = ?",
                (app_name,),
            ).fetchone()
        if row is None:
            return None
        description, fetched_at, success = row
        age_s = time.time() - fetched_at
        still_fresh = (
            (bool(success) and age_s < fresh_after_s)
            or (not bool(success) and age_s < retry_after_s)
        )
        return (description if success else None, still_fresh)

    def put_app_enrichment(
        self, app_name: str, description: str | None, success: bool,
    ) -> None:
        if not self._enabled or not self._conn:
            return
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO app_enrichment "
                "(app_name, description, fetched_at, success) "
                "VALUES (?, ?, ?, ?)",
                (app_name, description, time.time(), 1 if success else 0),
            )
            self._conn.commit()

    def log_mood(self, mood: str) -> None:
        """Record a mood-check response."""
        if not self._enabled or not self._conn:
            return
        with self._lock:
            self._conn.execute(
                "INSERT INTO mood_log (timestamp, mood) VALUES (?, ?)",
                (time.time(), mood),
            )
            self._conn.commit()
