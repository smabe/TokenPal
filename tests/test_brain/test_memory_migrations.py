"""Tests for memory.db PRAGMA user_version migration scaffolding."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from tokenpal.brain.memory import (
    _MIGRATIONS,
    _SCHEMA,
    CURRENT_SCHEMA_VERSION,
    MemoryStore,
)
from tokenpal.brain.schedule import Schedule


def _read_user_version(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("PRAGMA user_version").fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def test_fresh_db_sets_current_schema_version(tmp_path: Path) -> None:
    db = tmp_path / "m.db"
    store = MemoryStore(db)
    store.setup()
    store.teardown()
    assert _read_user_version(db) == CURRENT_SCHEMA_VERSION


def test_migrations_idempotent_on_second_setup(tmp_path: Path) -> None:
    db = tmp_path / "m.db"
    for _ in range(2):
        store = MemoryStore(db)
        store.setup()
        store.teardown()
    assert _read_user_version(db) == CURRENT_SCHEMA_VERSION


def test_v0_db_upgrades_cleanly(tmp_path: Path) -> None:
    """Simulate a pre-migration db: legacy schema only, user_version = 0."""
    db = tmp_path / "m.db"
    conn = sqlite3.connect(str(db))
    try:
        # Minimal legacy table to prove the upgrade path doesn't clobber data.
        conn.execute(
            "CREATE TABLE observations ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "timestamp REAL NOT NULL, sense_name TEXT NOT NULL, "
            "event_type TEXT NOT NULL, summary TEXT NOT NULL, "
            "data_json TEXT, session_id TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO observations "
            "(timestamp, sense_name, event_type, summary, session_id) "
            "VALUES (?, 'legacy', 'legacy', 'pre-existing', 'old-session')",
            (time.time(),),
        )
        conn.commit()
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == 0
    finally:
        conn.close()

    store = MemoryStore(db)
    store.setup()
    # Migrated tables exist
    assert store._conn is not None
    rows = store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='session_summaries'"
    ).fetchall()
    assert rows, "session_summaries should exist after migration"
    # Legacy row preserved
    legacy = store._conn.execute(
        "SELECT summary FROM observations WHERE sense_name='legacy'"
    ).fetchone()
    assert legacy is not None
    assert legacy[0] == "pre-existing"
    store.teardown()

    assert _read_user_version(db) == CURRENT_SCHEMA_VERSION


def test_session_summaries_round_trip(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "m.db")
    store.setup()
    store.record_summary("test handoff", window_start=100.0, window_end=200.0)
    latest = store.get_latest_summary(max_lookback_s=3600 * 24 * 365)
    assert latest is not None
    assert latest[1] == "test handoff"
    recent = store.get_recent_summaries(since_ts=0.0, limit=5)
    assert len(recent) == 1
    assert recent[0][1] == "test handoff"
    store.teardown()


def test_get_latest_summary_respects_lookback(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "m.db")
    store.setup()
    # Manually insert a very old summary
    assert store._conn is not None
    store._conn.execute(
        "INSERT INTO session_summaries "
        "(timestamp, session_id, window_start, window_end, summary) "
        "VALUES (1.0, 'old', 0.0, 1.0, 'ancient')"
    )
    store._conn.commit()
    assert store.get_latest_summary(max_lookback_s=10.0) is None
    store.teardown()


def _make_v4_db(db_path: Path) -> None:
    """Write a real v4 db: the frozen base schema plus migrations 1-4.

    Built by running the migration functions rather than re-typing their DDL,
    so the fixture cannot drift into a file no user ever had.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(_SCHEMA)
        for migration in _MIGRATIONS[:4]:
            migration(conn)
        conn.execute(
            "INSERT INTO chat_log (timestamp, speaker, text) "
            "VALUES (?, 'You', 'pre-existing')",
            (time.time(),),
        )
        conn.execute("PRAGMA user_version = 4")
        conn.commit()
    finally:
        conn.close()


def test_v4_db_upgrades_and_gains_reminders(tmp_path: Path) -> None:
    db = tmp_path / "m.db"
    _make_v4_db(db)

    store = MemoryStore(db)
    store.setup()
    try:
        assert store._conn is not None
        row = store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='reminders'"
        ).fetchone()
        assert row is not None, "reminders should exist after migration 5"
        prior = store._conn.execute(
            "SELECT text FROM chat_log WHERE speaker='You'"
        ).fetchone()
        assert prior is not None and prior[0] == "pre-existing"
    finally:
        store.teardown()

    assert _read_user_version(db) == CURRENT_SCHEMA_VERSION


def test_reminder_round_trip_through_sqlite(tmp_path: Path) -> None:
    """A Schedule survives to_row -> sqlite -> list_reminders -> from_row."""
    db = tmp_path / "m.db"
    store = MemoryStore(db)
    store.setup()
    daily = Schedule.daily_from_hhmm("22:45")
    interval = Schedule.interval_from_minutes(30)
    store.upsert_reminder("r-daily", "wind down", daily.to_row(), next_due_at=2000.0)
    store.upsert_reminder("r-interval", "stand up", interval.to_row(), next_due_at=1000.0)
    store.teardown()

    # Fresh store over the same file — the restart this table exists for.
    reopened = MemoryStore(db)
    reopened.setup()
    try:
        rows = reopened.list_reminders()
        assert [r["id"] for r in rows] == ["r-interval", "r-daily"]
        assert Schedule.from_row(rows[0]) == interval
        assert Schedule.from_row(rows[1]) == daily
        assert rows[1]["label"] == "wind down"
        assert rows[1]["next_due_at"] == 2000.0
        assert rows[1]["last_fired_at"] is None
        assert rows[1]["armed_at"] > 0.0

        reopened.mark_reminder_fired("r-daily", fired_at=2000.0, next_due_at=88400.0)
        fired = [r for r in reopened.list_reminders() if r["id"] == "r-daily"][0]
        assert fired["last_fired_at"] == 2000.0
        assert fired["next_due_at"] == 88400.0

        assert reopened.delete_reminder("r-daily") is True
        assert reopened.delete_reminder("r-daily") is False
        assert [r["id"] for r in reopened.list_reminders()] == ["r-interval"]
    finally:
        reopened.teardown()


def test_upsert_reminder_twice_on_one_id_leaves_one_row(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "m.db")
    store.setup()
    try:
        schedule = Schedule.interval_from_minutes(10)
        store.upsert_reminder("r1", "first", schedule.to_row(), next_due_at=1.0)
        store.upsert_reminder("r1", "second", schedule.to_row(), next_due_at=2.0)
        rows = store.list_reminders()
        assert len(rows) == 1
        assert rows[0]["label"] == "second"
        assert rows[0]["next_due_at"] == 2.0
    finally:
        store.teardown()


def test_prune_leaves_reminders_untouched(tmp_path: Path) -> None:
    """A reminder armed longer ago than retention_days is not swept at startup."""
    db = tmp_path / "m.db"
    store = MemoryStore(db, retention_days=30)
    store.setup()
    ancient = time.time() - 90 * 86400
    store.upsert_reminder(
        "r-old",
        "hydrate",
        Schedule.interval_from_minutes(60).to_row(),
        next_due_at=ancient + 3600,
    )
    assert store._conn is not None
    store._conn.execute("UPDATE reminders SET armed_at = ?", (ancient,))
    store._conn.commit()
    store.teardown()

    reopened = MemoryStore(db, retention_days=30)
    reopened.setup()  # runs _prune()
    try:
        assert [r["id"] for r in reopened.list_reminders()] == ["r-old"]
    finally:
        reopened.teardown()


def test_rearming_preserves_fire_history(tmp_path: Path) -> None:
    """Editing a live reminder must not erase that it already fired."""
    store = MemoryStore(tmp_path / "m.db")
    store.setup()
    try:
        schedule = Schedule.interval_from_minutes(60)
        store.upsert_reminder("r1", "stand up", schedule.to_row(), next_due_at=1000.0)
        armed_at = store.list_reminders()[0]["armed_at"]
        assert store.mark_reminder_fired("r1", fired_at=500.0, next_due_at=2000.0) is True

        faster = Schedule.interval_from_minutes(30)
        store.upsert_reminder("r1", "stretch", faster.to_row(), next_due_at=3000.0)
        row = store.list_reminders()[0]
        assert row["label"] == "stretch"
        assert row["interval_s"] == 1800.0
        assert row["next_due_at"] == 3000.0
        assert row["last_fired_at"] == 500.0
        assert row["armed_at"] == armed_at
    finally:
        store.teardown()


def test_mark_reminder_fired_reports_a_missing_row(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "m.db")
    store.setup()
    try:
        assert store.mark_reminder_fired("ghost", fired_at=1.0, next_due_at=2.0) is False
    finally:
        store.teardown()


def test_upsert_reminder_refuses_a_row_from_row_could_not_read(tmp_path: Path) -> None:
    """A row Schedule.from_row rejects is unreadable forever and unrepairable."""
    store = MemoryStore(tmp_path / "m.db")
    store.setup()
    try:
        with pytest.raises(ValueError, match="cannot both be set"):
            store.upsert_reminder(
                "poison",
                "x",
                {"kind": "interval", "interval_s": 600.0, "at_hour": 0, "at_minute": 0},
                next_due_at=1.0,
            )
        with pytest.raises(ValueError, match="unknown schedule kind"):
            store.upsert_reminder("w", "x", {"kind": "weekly"}, next_due_at=1.0)
        assert store.list_reminders() == []
    finally:
        store.teardown()
