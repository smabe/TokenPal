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
        self._callback_cache: list[str] | None = None

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
        self.aggregate_daily_summaries()
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

    def get_app_enrichment(
        self, app_name: str,
    ) -> tuple[str | None, float, bool] | None:
        """Return ``(description, age_s, success)`` for a cached app, else None."""
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
        return (description, time.time() - fetched_at, bool(success))

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
