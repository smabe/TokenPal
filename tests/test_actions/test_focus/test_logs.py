"""Tests for hydration_log / habit_streak / mood_check."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tokenpal.actions.focus.logs import (
    HabitStreakAction,
    HydrationLogAction,
    MoodCheckAction,
)
from tokenpal.brain.memory import MemoryStore


@pytest.fixture()
def memory(tmp_path: Path) -> MemoryStore:
    m = MemoryStore(db_path=tmp_path / "test.db")
    m.setup()
    yield m
    m.teardown()


async def test_hydration_log_accumulates(memory: MemoryStore) -> None:
    action = HydrationLogAction({"memory": memory})
    r = await action.execute(amount_oz=8)
    assert r.success
    assert "8oz" in r.output
    r = await action.execute(amount_oz=12.5)
    # 8 + 12.5 = 20.5 -> banker's rounding via {:.0f} gives "20".
    assert "20oz" in r.output
    assert memory.get_hydration_today() == pytest.approx(20.5)


async def test_hydration_rejects_bad_amount(memory: MemoryStore) -> None:
    action = HydrationLogAction({"memory": memory})
    assert not (await action.execute(amount_oz=-1)).success
    assert not (await action.execute(amount_oz=0)).success
    assert not (await action.execute(amount_oz=9999)).success


async def test_hydration_requires_memory() -> None:
    action = HydrationLogAction({})
    r = await action.execute(amount_oz=8)
    assert not r.success


async def test_habit_streak_counts_consecutive_days(memory: MemoryStore) -> None:
    today = datetime.now().date()
    # Log 5 consecutive days ending today.
    for i in range(5):
        d = today - timedelta(days=i)
        memory.log_habit("journal", date=d.strftime("%Y-%m-%d"))
    # Also log 3 consecutive days a couple weeks back to test "longest".
    for i in range(3):
        d = today - timedelta(days=30 + i)
        memory.log_habit("journal", date=d.strftime("%Y-%m-%d"))

    action = HabitStreakAction({"memory": memory})
    r = await action.execute(name="journal", log=False)
    assert r.success
    # Current streak should be 5, longest at least 5 (current wins).
    assert "5 day streak" in r.output
    assert "longest 5" in r.output


async def test_habit_streak_broken_by_gap(memory: MemoryStore) -> None:
    today = datetime.now().date()
    # Yesterday, but skip today — streak should be 2 (yesterday + the day before)
    # and current should be 2 because "today" was yesterday's last entry
    # (gap==1 is still a live streak).
    for i in range(1, 3):
        d = today - timedelta(days=i)
        memory.log_habit("gym", date=d.strftime("%Y-%m-%d"))
    action = HabitStreakAction({"memory": memory})
    r = await action.execute(name="gym", log=False)
    assert "2 day streak" in r.output

    # Now put a big gap: only a log from 10 days ago -> current streak 0.
    memory.log_habit(
        "old_habit", date=(today - timedelta(days=10)).strftime("%Y-%m-%d")
    )
    r = await action.execute(name="old_habit", log=False)
    assert "0 day streak" in r.output


async def test_habit_log_today_advances_streak(memory: MemoryStore) -> None:
    action = HabitStreakAction({"memory": memory})
    r = await action.execute(name="reading")  # default log=True
    assert r.success
    assert "1 day streak" in r.output


async def test_mood_check_prompt_and_submit(memory: MemoryStore) -> None:
    action = MoodCheckAction({"memory": memory})
    prompt = await action.execute()
    assert prompt.success
    assert "feeling" in prompt.output.lower()

    submit = await action.execute(mood="focused")
    assert submit.success
    assert "focused" in submit.output


async def test_mood_check_without_prompt_is_unstored(memory: MemoryStore) -> None:
    action = MoodCheckAction({"memory": memory})
    # No prior prompt; submission should be acknowledged but not stored.
    r = await action.execute(mood="tired")
    assert r.success
    assert "not stored" in r.output.lower()
