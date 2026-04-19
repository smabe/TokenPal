"""Tests for the chat_log table — round-trip insert, tail read, clear,
trim-on-insert cap, and parameterization safety.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from tokenpal.brain.memory import CURRENT_SCHEMA_VERSION, MemoryStore


def test_chat_log_table_exists_after_setup(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "m.db")
    store.setup()
    try:
        conn = sqlite3.connect(str(tmp_path / "m.db"))
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='chat_log'"
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert CURRENT_SCHEMA_VERSION >= 3
    finally:
        store.teardown()


def test_round_trip_insert_and_tail(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "m.db")
    store.setup()
    store.set_chat_log_max_persisted(10)
    try:
        store.record_chat_entry("You", "hello")
        store.record_chat_entry("Buddy", "hi there")
        store.record_chat_entry("Buddy", "found it", "https://example.com/a")
        rows = store.get_recent_chat_entries(10)
    finally:
        store.teardown()

    assert len(rows) == 3
    # chronological order
    assert [r[1] for r in rows] == ["You", "Buddy", "Buddy"]
    assert [r[2] for r in rows] == ["hello", "hi there", "found it"]
    assert rows[0][3] is None
    assert rows[2][3] == "https://example.com/a"


def test_clear_wipes_all_rows(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "m.db")
    store.setup()
    store.set_chat_log_max_persisted(10)
    try:
        for i in range(5):
            store.record_chat_entry("You", f"msg {i}")
        assert len(store.get_recent_chat_entries(10)) == 5
        store.clear_chat_log()
        assert store.get_recent_chat_entries(10) == []
    finally:
        store.teardown()


def test_trim_on_insert_enforces_cap(tmp_path: Path) -> None:
    """Trim fires once count exceeds max_persisted * 1.5. Final count is
    exactly max_persisted (we delete count - max_persisted rows)."""
    store = MemoryStore(tmp_path / "m.db")
    store.setup()
    cap = 10
    store.set_chat_log_max_persisted(cap)
    try:
        for i in range(cap * 2):
            store.record_chat_entry("You", f"msg {i}")
        rows = store.get_recent_chat_entries(100)
    finally:
        store.teardown()

    # After 20 inserts with cap=10: trim fires at row 16 (> 15), bringing
    # us back to 10. Rows 16-19 then land without further trim.
    assert len(rows) <= int(cap * 1.5)
    assert len(rows) >= cap


def test_sql_injection_safe(tmp_path: Path) -> None:
    """Parameterized placeholders — a malicious-looking text stays data."""
    store = MemoryStore(tmp_path / "m.db")
    store.setup()
    store.set_chat_log_max_persisted(10)
    try:
        store.record_chat_entry("You", "1); DROP TABLE chat_log; --")
        rows = store.get_recent_chat_entries(10)
    finally:
        store.teardown()
    assert len(rows) == 1
    assert rows[0][2] == "1); DROP TABLE chat_log; --"
    # table still exists — verify by reopening the store.
    store2 = MemoryStore(tmp_path / "m.db")
    store2.setup()
    try:
        assert len(store2.get_recent_chat_entries(10)) == 1
    finally:
        store2.teardown()


def test_max_persisted_zero_skips_insert(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "m.db")
    store.setup()
    store.set_chat_log_max_persisted(0)
    try:
        store.record_chat_entry("You", "skipped")
        rows = store.get_recent_chat_entries(10)
    finally:
        store.teardown()
    assert rows == []


def test_count_survives_restart(tmp_path: Path) -> None:
    """In-memory counter is primed from SELECT COUNT(*) on setup, so a
    fresh store instance keeps trim semantics intact."""
    db = tmp_path / "m.db"
    first = MemoryStore(db)
    first.setup()
    first.set_chat_log_max_persisted(3)
    for i in range(5):
        first.record_chat_entry("You", f"msg {i}")
    first.teardown()

    second = MemoryStore(db)
    second.setup()
    assert second._chat_log_count == len(second.get_recent_chat_entries(100))
    second.teardown()


def test_disabled_store_is_noop(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "m.db", enabled=False)
    store.setup()
    store.set_chat_log_max_persisted(10)
    store.record_chat_entry("You", "hello")
    assert store.get_recent_chat_entries(10) == []
    store.clear_chat_log()
