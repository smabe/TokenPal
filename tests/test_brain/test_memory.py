"""Tests for MemoryStore — daily aggregation and pattern detection."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tokenpal.brain.memory import MemoryStore


@pytest.fixture()
def store(tmp_path: Path) -> MemoryStore:
    """Create a MemoryStore with an in-memory-style temp DB."""
    s = MemoryStore(tmp_path / "test_memory.db")
    s.setup()
    return s


def _insert_app_switch(
    store: MemoryStore,
    app: str,
    ts: float,
    session_id: str = "s1",
) -> None:
    """Insert an app_switch observation at a specific timestamp."""
    assert store._conn is not None
    store._conn.execute(
        "INSERT INTO observations "
        "(timestamp, sense_name, event_type, summary, data_json, session_id) "
        "VALUES (?, 'app_awareness', 'app_switch', ?, NULL, ?)",
        (ts, app, session_id),
    )
    store._conn.commit()


def _ts(date_str: str, hour: int = 12) -> float:
    """Convert 'YYYY-MM-DD' + hour to a unix timestamp."""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(hour=hour)
    return dt.timestamp()


# ------------------------------------------------------------------
# Daily aggregation
# ------------------------------------------------------------------


class TestDailyAggregation:
    def test_backfills_missing_dates(self, store: MemoryStore) -> None:
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        _insert_app_switch(store, "VS Code", _ts(yesterday), "s1")
        _insert_app_switch(store, "Chrome", _ts(yesterday), "s1")

        store.aggregate_daily_summaries()

        assert store._conn is not None
        row = store._conn.execute(
            "SELECT * FROM daily_summaries WHERE date = ?", (yesterday,)
        ).fetchone()
        assert row is not None
        assert "VS Code" in row[1]  # summary text

    def test_skips_today(self, store: MemoryStore) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        _insert_app_switch(store, "VS Code", _ts(today), "s1")

        store.aggregate_daily_summaries()

        assert store._conn is not None
        row = store._conn.execute(
            "SELECT * FROM daily_summaries WHERE date = ?", (today,)
        ).fetchone()
        assert row is None

    def test_idempotent(self, store: MemoryStore) -> None:
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        _insert_app_switch(store, "VS Code", _ts(yesterday), "s1")

        store.aggregate_daily_summaries()
        store.aggregate_daily_summaries()  # second call is a no-op

        assert store._conn is not None
        rows = store._conn.execute(
            "SELECT COUNT(*) FROM daily_summaries WHERE date = ?",
            (yesterday,),
        ).fetchone()
        assert rows[0] == 1

    def test_top_apps_json(self, store: MemoryStore) -> None:
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        for _ in range(5):
            _insert_app_switch(store, "VS Code", _ts(yesterday), "s1")
        for _ in range(3):
            _insert_app_switch(store, "Chrome", _ts(yesterday), "s1")

        store.aggregate_daily_summaries()

        assert store._conn is not None
        row = store._conn.execute(
            "SELECT top_apps FROM daily_summaries WHERE date = ?",
            (yesterday,),
        ).fetchone()
        top_apps = json.loads(row[0])
        assert top_apps["VS Code"] == 5
        assert top_apps["Chrome"] == 3


# ------------------------------------------------------------------
# Pattern detection: _is_sensitive
# ------------------------------------------------------------------


class TestIsSensitive:
    def test_matches_substring(self) -> None:
        assert MemoryStore._is_sensitive("1Password", {"1password"})

    def test_case_insensitive(self) -> None:
        assert MemoryStore._is_sensitive("Signal", {"signal"})

    def test_no_match(self) -> None:
        assert not MemoryStore._is_sensitive("VS Code", {"signal", "chase"})


# ------------------------------------------------------------------
# Pattern detection: day-of-week
# ------------------------------------------------------------------


class TestDayOfWeekPatterns:
    def test_detects_weekday_skew(self, store: MemoryStore) -> None:
        # Find a known Monday, seed heavy Monday usage
        # 2026-04-13 is a Monday
        monday = "2026-04-13"
        assert datetime.strptime(monday, "%Y-%m-%d").weekday() == 0  # Monday

        # 6 visits on Monday, 1 each on Tue-Thu
        for i in range(6):
            _insert_app_switch(store, "Twitter", _ts(monday, 9 + i), f"mon{i}")
        _insert_app_switch(store, "Twitter", _ts("2026-04-07", 10), "tue1")
        _insert_app_switch(store, "Twitter", _ts("2026-04-08", 10), "wed1")
        _insert_app_switch(store, "Twitter", _ts("2026-04-09", 10), "thu1")

        callbacks = store._detect_day_of_week_patterns(set())
        assert len(callbacks) == 1
        assert "Monday" in callbacks[0]
        assert "Twitter" in callbacks[0]

    def test_no_skew_when_uniform(self, store: MemoryStore) -> None:
        # Spread evenly across days — no pattern
        base = datetime(2026, 4, 6)  # Monday
        for day_offset in range(7):
            d = (base + timedelta(days=day_offset)).strftime("%Y-%m-%d")
            _insert_app_switch(store, "Chrome", _ts(d), f"s{day_offset}")

        callbacks = store._detect_day_of_week_patterns(set())
        assert callbacks == []

    def test_excludes_sensitive_apps(self, store: MemoryStore) -> None:
        monday = "2026-04-13"
        for i in range(6):
            _insert_app_switch(store, "Signal", _ts(monday, 9 + i), f"m{i}")

        callbacks = store._detect_day_of_week_patterns({"signal"})
        assert callbacks == []


# ------------------------------------------------------------------
# Pattern detection: first-app-per-session
# ------------------------------------------------------------------


class TestTimeOfDayPatterns:
    def test_detects_consistent_first_app(self, store: MemoryStore) -> None:
        base = datetime(2026, 4, 8)
        for i in range(5):
            d = (base + timedelta(days=i)).strftime("%Y-%m-%d")
            sid = f"session{i}"
            _insert_app_switch(store, "Slack", _ts(d, 9), sid)
            _insert_app_switch(store, "VS Code", _ts(d, 10), sid)

        callbacks = store._detect_time_of_day_patterns(set())
        assert len(callbacks) == 1
        assert "Slack" in callbacks[0]
        assert "first" in callbacks[0]

    def test_no_pattern_when_varied(self, store: MemoryStore) -> None:
        apps = ["Slack", "Chrome", "Twitter", "VS Code", "Discord"]
        base = datetime(2026, 4, 8)
        for i, app in enumerate(apps):
            d = (base + timedelta(days=i)).strftime("%Y-%m-%d")
            _insert_app_switch(store, app, _ts(d, 9), f"s{i}")

        callbacks = store._detect_time_of_day_patterns(set())
        assert callbacks == []

    def test_needs_minimum_sessions(self, store: MemoryStore) -> None:
        # Only 2 sessions — below threshold
        for i in range(2):
            _insert_app_switch(
                store, "Slack", _ts(f"2026-04-0{8+i}", 9), f"s{i}"
            )

        callbacks = store._detect_time_of_day_patterns(set())
        assert callbacks == []


# ------------------------------------------------------------------
# Pattern detection: streaks
# ------------------------------------------------------------------


class TestStreaks:
    def test_detects_consecutive_days(self, store: MemoryStore) -> None:
        today = datetime.now()
        for i in range(5):
            d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            _insert_app_switch(store, "VS Code", _ts(d), f"s{i}")

        callbacks = store._detect_streaks(set())
        assert len(callbacks) == 1
        assert "VS Code" in callbacks[0]
        assert "5 consecutive" in callbacks[0]

    def test_streak_must_be_recent(self, store: MemoryStore) -> None:
        # Streak ended 10 days ago — should not be reported
        old_base = datetime.now() - timedelta(days=15)
        for i in range(5):
            d = (old_base + timedelta(days=i)).strftime("%Y-%m-%d")
            _insert_app_switch(store, "VS Code", _ts(d), f"s{i}")

        callbacks = store._detect_streaks(set())
        assert callbacks == []

    def test_minimum_streak_length(self, store: MemoryStore) -> None:
        # Only 2 consecutive days — below threshold of 3
        today = datetime.now()
        for i in range(2):
            d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            _insert_app_switch(store, "VS Code", _ts(d), f"s{i}")

        callbacks = store._detect_streaks(set())
        assert callbacks == []

    def test_excludes_sensitive(self, store: MemoryStore) -> None:
        today = datetime.now()
        for i in range(5):
            d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            _insert_app_switch(store, "Chase", _ts(d), f"s{i}")

        callbacks = store._detect_streaks({"chase"})
        assert callbacks == []


# ------------------------------------------------------------------
# Pattern detection: rituals
# ------------------------------------------------------------------


class TestRituals:
    def test_detects_recurring_sequence(self, store: MemoryStore) -> None:
        base = datetime(2026, 4, 8)
        for i in range(4):
            d = (base + timedelta(days=i)).strftime("%Y-%m-%d")
            sid = f"s{i}"
            _insert_app_switch(store, "Slack", _ts(d, 9), sid)
            _insert_app_switch(store, "GitHub", _ts(d, 10), sid)
            _insert_app_switch(store, "VS Code", _ts(d, 11), sid)

        callbacks = store._detect_rituals(set())
        assert len(callbacks) == 1
        assert "Slack" in callbacks[0]
        assert "GitHub" in callbacks[0]
        assert "VS Code" in callbacks[0]

    def test_no_ritual_when_varied(self, store: MemoryStore) -> None:
        apps_per_session = [
            ["Slack", "GitHub", "VS Code"],
            ["Chrome", "Twitter", "Slack"],
            ["VS Code", "GitHub", "Chrome"],
            ["Twitter", "Slack", "GitHub"],
        ]
        base = datetime(2026, 4, 8)
        for i, apps in enumerate(apps_per_session):
            d = (base + timedelta(days=i)).strftime("%Y-%m-%d")
            for j, app in enumerate(apps):
                _insert_app_switch(store, app, _ts(d, 9 + j), f"s{i}")

        callbacks = store._detect_rituals(set())
        assert callbacks == []


# ------------------------------------------------------------------
# get_pattern_callbacks (integration)
# ------------------------------------------------------------------


class TestGetPatternCallbacks:
    def test_caches_results(self, store: MemoryStore) -> None:
        result1 = store.get_pattern_callbacks()
        result2 = store.get_pattern_callbacks()
        assert result1 is result2

    def test_respects_max_callbacks(self, store: MemoryStore) -> None:
        today = datetime.now()
        # Seed enough data for multiple patterns
        for i in range(6):
            d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            sid = f"s{i}"
            _insert_app_switch(store, "VS Code", _ts(d, 9), sid)
            _insert_app_switch(store, "Slack", _ts(d, 10), sid)
            _insert_app_switch(store, "Chrome", _ts(d, 11), sid)

        callbacks = store.get_pattern_callbacks(max_callbacks=1)
        assert len(callbacks) <= 1

    def test_empty_when_disabled(self, tmp_path: Path) -> None:
        s = MemoryStore(tmp_path / "disabled.db", enabled=False)
        s.setup()
        assert s.get_pattern_callbacks() == []

    def test_passes_sensitive_apps(self, store: MemoryStore) -> None:
        today = datetime.now()
        for i in range(5):
            d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            _insert_app_switch(store, "Signal", _ts(d), f"s{i}")

        # Without filtering — should detect streak
        callbacks = store.get_pattern_callbacks(sensitive_apps=[])
        has_signal = any("Signal" in cb for cb in callbacks)

        # Reset cache and try with filtering
        store._callback_cache = None
        callbacks = store.get_pattern_callbacks(sensitive_apps=["signal"])
        has_signal_filtered = any("Signal" in cb for cb in callbacks)

        assert has_signal
        assert not has_signal_filtered


class TestToolCalls:
    def test_record_and_query(self, store: MemoryStore) -> None:
        store.record_tool_call("timer", 12.5, True)
        store.record_tool_call("timer", 8.0, True)
        store.record_tool_call("do_math", 0.3, True)
        store.record_tool_call("do_math", 0.4, False)

        counts = store.tool_usage_counts()
        assert counts == {"timer": 2, "do_math": 1}

    def test_since_days_filter(self, store: MemoryStore) -> None:
        assert store._conn is not None
        old = time.time() - 10 * 86400
        store._conn.execute(
            "INSERT INTO tool_calls (timestamp, tool_name, duration_ms, success) "
            "VALUES (?, 'git_log', 5.0, 1)",
            (old,),
        )
        store._conn.commit()
        store.record_tool_call("git_log", 5.0, True)

        assert store.tool_usage_counts()["git_log"] == 2
        assert store.tool_usage_counts(since_days=7)["git_log"] == 1


class TestResearchCache:
    def test_hit_and_miss(self, store: MemoryStore) -> None:
        assert store.get_research_answer("abc", 3600) is None
        store.cache_research_answer("abc", "what?", "yes.", '[]')
        hit = store.get_research_answer("abc", 3600)
        assert hit is not None
        answer, sources_json, age = hit
        assert answer == "yes."
        assert sources_json == "[]"
        assert age < 1.0

    def test_expiry(self, store: MemoryStore) -> None:
        assert store._conn is not None
        store.cache_research_answer("xyz", "q", "a", "[]")
        store._conn.execute(
            "UPDATE research_cache SET created_at = ? WHERE question_hash = 'xyz'",
            (time.time() - 10,),
        )
        store._conn.commit()
        assert store.get_research_answer("xyz", max_age_s=5.0) is None
        assert store.get_research_answer("xyz", max_age_s=30.0) is not None

    def test_get_latest_research_returns_most_recent(
        self, store: MemoryStore,
    ) -> None:
        """Used by /refine - picks the latest research regardless of hash."""
        assert store.get_latest_research(3600) is None
        store.cache_research_answer("h1", "first question", "first answer", "[]")
        # Space them apart so ordering is deterministic.
        assert store._conn is not None
        store._conn.execute(
            "UPDATE research_cache SET created_at = ? WHERE question_hash = 'h1'",
            (time.time() - 60,),
        )
        store._conn.commit()
        store.cache_research_answer("h2", "second question", "second answer", '[{"url": "x"}]')
        hit = store.get_latest_research(3600)
        assert hit is not None
        question, answer, sources_json, age = hit
        assert question == "second question"
        assert answer == "second answer"
        assert sources_json == '[{"url": "x"}]'
        assert age < 5.0

    def test_get_latest_research_respects_max_age(
        self, store: MemoryStore,
    ) -> None:
        store.cache_research_answer("old", "q", "a", "[]")
        assert store._conn is not None
        store._conn.execute(
            "UPDATE research_cache SET created_at = ? WHERE question_hash = 'old'",
            (time.time() - 7200,),  # 2h old
        )
        store._conn.commit()
        # max_age 1h should filter it out
        assert store.get_latest_research(3600) is None
        assert store.get_latest_research(10800) is not None


# ------------------------------------------------------------------
# LLM throughput estimator persistence
# ------------------------------------------------------------------


class TestThroughputEstimator:
    def test_missing_row_returns_none(self, store: MemoryStore) -> None:
        assert store.get_llm_throughput_estimator("http://x/v1", "gemma4") is None

    def test_save_then_get_roundtrip(self, store: MemoryStore) -> None:
        store.save_llm_throughput_estimator("http://x/v1", "gemma4", 57.0, 0.8, 50)
        row = store.get_llm_throughput_estimator("http://x/v1", "gemma4")
        assert row == (57.0, 0.8, 50)

    def test_upsert_overwrites_prior_row(self, store: MemoryStore) -> None:
        store.save_llm_throughput_estimator("http://x/v1", "gemma4", 57.0, 0.8, 50)
        store.save_llm_throughput_estimator("http://x/v1", "gemma4", 62.0, 0.9, 120)
        row = store.get_llm_throughput_estimator("http://x/v1", "gemma4")
        assert row == (62.0, 0.9, 120)

    def test_rows_scoped_by_server_and_model(self, store: MemoryStore) -> None:
        store.save_llm_throughput_estimator("http://x/v1", "gemma4", 57.0, 0.8, 50)
        store.save_llm_throughput_estimator("http://x/v1", "qwen3", 30.0, 1.5, 40)
        store.save_llm_throughput_estimator("http://y/v1", "gemma4", 80.0, 0.3, 200)
        assert store.get_llm_throughput_estimator("http://x/v1", "gemma4") == (57.0, 0.8, 50)
        assert store.get_llm_throughput_estimator("http://x/v1", "qwen3") == (30.0, 1.5, 40)
        assert store.get_llm_throughput_estimator("http://y/v1", "gemma4") == (80.0, 0.3, 200)

    def test_stale_schema_version_ignored(self, store: MemoryStore) -> None:
        assert store._conn is not None
        store._conn.execute(
            "INSERT INTO llm_throughput_estimators "
            "(server_url, model, decode_tps, ttft_s, sample_count, updated_at, schema_version) "
            "VALUES ('http://x/v1', 'gemma4', 57.0, 0.8, 50, ?, 0)",
            (time.time(),),
        )
        store._conn.commit()
        assert store.get_llm_throughput_estimator("http://x/v1", "gemma4") is None
