"""Tests for memory.db PRAGMA user_version migration scaffolding."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from tokenpal.brain.memory import CURRENT_SCHEMA_VERSION, MemoryStore


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
