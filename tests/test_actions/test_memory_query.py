"""Tests for memory_query."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from tokenpal.actions.memory_query import MemoryQueryAction


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            sense_name TEXT NOT NULL,
            event_type TEXT NOT NULL,
            summary TEXT NOT NULL,
            data_json TEXT,
            session_id TEXT NOT NULL
        );
        """
    )
    now = time.time()
    rows = [
        (now - 3600, "app_awareness", "app_switch", "Terminal", None, "s1"),
        (now - 1800, "app_awareness", "app_switch", "Terminal", None, "s1"),
        (now - 900, "app_awareness", "app_switch", "Firefox", None, "s1"),
        (now - 600, "app_awareness", "app_switch", "1Password", None, "s1"),
        (now - 300, "app_awareness", "app_switch", "Terminal", None, "s2"),
    ]
    conn.executemany(
        "INSERT INTO observations "
        "(timestamp, sense_name, event_type, summary, data_json, session_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


@pytest.fixture
def db_dir(tmp_path: Path) -> Path:
    _make_db(tmp_path / "memory.db")
    return tmp_path


async def test_memory_query_time_in_app(db_dir: Path) -> None:
    action = MemoryQueryAction({"data_dir": str(db_dir)})
    result = await action.execute(metric="time_in_app")
    assert result.success is True
    assert "Terminal" in result.output
    assert "1Password" not in result.output


async def test_memory_query_switches_per_hour(db_dir: Path) -> None:
    result = await MemoryQueryAction({"data_dir": str(db_dir)}).execute(
        metric="switches_per_hour"
    )
    assert result.success is True
    assert "switch" in result.output


async def test_memory_query_session_count_today(db_dir: Path) -> None:
    result = await MemoryQueryAction({"data_dir": str(db_dir)}).execute(
        metric="session_count_today"
    )
    assert result.success is True
    assert "session" in result.output


async def test_memory_query_streaks(db_dir: Path) -> None:
    result = await MemoryQueryAction({"data_dir": str(db_dir)}).execute(metric="streaks")
    assert result.success is True


async def test_memory_query_rejects_unknown_metric(db_dir: Path) -> None:
    result = await MemoryQueryAction({"data_dir": str(db_dir)}).execute(
        metric="drop_table"
    )
    assert result.success is False


async def test_memory_query_missing_db(tmp_path: Path) -> None:
    result = await MemoryQueryAction({"data_dir": str(tmp_path)}).execute(
        metric="time_in_app"
    )
    assert result.success is False
