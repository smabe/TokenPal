"""Tests for the Schedule value type — interval and time-of-day maths, the
DST answers naive local arithmetic actually gives, parsing and round-trip.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import datetime

import pytest

from tokenpal.brain.schedule import Schedule

# America/New_York in 2026: spring forward 2026-03-08 02:00 -> 03:00,
# fall back 2026-11-01 02:00 -> 01:00.


@pytest.fixture
def eastern(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin the process timezone so the DST cases are deterministic."""
    if not hasattr(time, "tzset"):
        pytest.skip("TZ pinning needs time.tzset (POSIX only)")
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    if time.tzname != ("EST", "EDT"):
        # An unresolvable zone silently falls back to UTC, where the DST cases
        # assert wrong answers instead of not running.
        pytest.skip("no tzdata for America/New_York")
    yield
    monkeypatch.undo()
    time.tzset()


def _local(*args: int) -> float:
    return datetime(*args).timestamp()


# ----------------------------------------------------------------------
# Interval
# ----------------------------------------------------------------------


def test_interval_next_due_is_after_plus_interval() -> None:
    s = Schedule.interval_from_minutes(25)
    assert s.interval_s == 1500.0
    assert s.next_due_at(1000.0) == 2500.0


def test_interval_bounds_rejected_at_both_ends() -> None:
    with pytest.raises(ValueError, match="whole number"):
        Schedule.interval_from_minutes(0)
    with pytest.raises(ValueError, match="whole number"):
        Schedule.interval_from_minutes(1441)
    # The bounds themselves are accepted.
    assert Schedule.interval_from_minutes(1).interval_s == 60.0
    assert Schedule.interval_from_minutes(1440).interval_s == 86400.0


# ----------------------------------------------------------------------
# Daily
# ----------------------------------------------------------------------


def test_daily_target_later_today(eastern: None) -> None:
    s = Schedule.daily_from_hhmm("21:30")
    due = s.next_due_at(_local(2026, 5, 1, 9, 0))
    assert due == _local(2026, 5, 1, 21, 30)


def test_daily_target_already_passed_rolls_to_tomorrow(eastern: None) -> None:
    s = Schedule.daily_from_hhmm("07:15")
    due = s.next_due_at(_local(2026, 5, 1, 9, 0))
    assert due == _local(2026, 5, 2, 7, 15)


def test_daily_target_exactly_now_rolls_to_tomorrow(eastern: None) -> None:
    """Strictly after: a reminder fired at its instant re-arms, not re-fires."""
    s = Schedule.daily_from_hhmm("09:00")
    now = _local(2026, 5, 1, 9, 0)
    assert s.next_due_at(now) == _local(2026, 5, 2, 9, 0)


def test_daily_spring_forward_nonexistent_time_resolves_to_0330(
    eastern: None,
) -> None:
    """02:30 does not exist on 2026-03-08; naive .replace() lands on 03:30 EDT."""
    s = Schedule.daily_from_hhmm("02:30")
    due = s.next_due_at(_local(2026, 3, 8, 0, 30))
    assert datetime.fromtimestamp(due) == datetime(2026, 3, 8, 3, 30)


def test_daily_fall_back_ambiguous_time_fires_on_the_first_pass(
    eastern: None,
) -> None:
    """01:30 happens twice on 2026-11-01; fold=0 (EDT, the earlier one) wins."""
    s = Schedule.daily_from_hhmm("01:30")
    due = s.next_due_at(_local(2026, 11, 1, 0, 30))
    assert due == datetime(2026, 11, 1, 1, 30, fold=0).timestamp()
    assert datetime(2026, 11, 1, 1, 30, fold=1).timestamp() - due == 3600.0


def test_daily_does_not_drift_across_dst(eastern: None) -> None:
    """Recomputed from the calendar, so the real gap is 23 h / 25 h, not 24 h."""
    s = Schedule.daily_from_hhmm("09:00")

    spring_first = s.next_due_at(_local(2026, 3, 7, 8, 0))
    spring_second = s.next_due_at(spring_first)
    assert spring_second - spring_first == 23 * 3600.0
    assert datetime.fromtimestamp(spring_second) == datetime(2026, 3, 8, 9, 0)

    fall_first = s.next_due_at(_local(2026, 10, 31, 8, 0))
    fall_second = s.next_due_at(fall_first)
    assert fall_second - fall_first == 25 * 3600.0
    assert datetime.fromtimestamp(fall_second) == datetime(2026, 11, 1, 9, 0)


# ----------------------------------------------------------------------
# Serialisation
# ----------------------------------------------------------------------


def test_round_trip_both_kinds() -> None:
    interval = Schedule.interval_from_minutes(45)
    daily = Schedule.daily_from_hhmm("23:00")
    assert Schedule.from_row(interval.to_row()) == interval
    assert Schedule.from_row(daily.to_row()) == daily
    assert interval.to_row() == {
        "kind": "interval",
        "interval_s": 2700.0,
        "at_hour": None,
        "at_minute": None,
    }


def test_from_row_ignores_extra_keys() -> None:
    row = {
        "id": "r1",
        "label": "stand up",
        "kind": "daily",
        "interval_s": None,
        "at_hour": 8,
        "at_minute": 5,
        "armed_at": 1.0,
        "next_due_at": 2.0,
        "last_fired_at": None,
    }
    assert Schedule.from_row(row) == Schedule(kind="daily", at_hour=8, at_minute=5)


def test_from_row_rejects_fields_that_do_not_match_kind() -> None:
    with pytest.raises(ValueError):
        Schedule.from_row(
            {"kind": "interval", "interval_s": 600.0, "at_hour": 8, "at_minute": 0}
        )
    with pytest.raises(ValueError):
        Schedule.from_row(
            {"kind": "daily", "interval_s": None, "at_hour": None, "at_minute": None}
        )
    with pytest.raises(ValueError):
        Schedule.from_row({"kind": "weekly", "interval_s": None})


# ----------------------------------------------------------------------
# Parsing — messages name the argument the tool exposes
# ----------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["25:70", "quarter past", "", "9", "23:00:00"])
def test_daily_from_hhmm_rejects_junk_naming_at(raw: str) -> None:
    with pytest.raises(ValueError, match="24-hour time"):
        Schedule.daily_from_hhmm(raw)


@pytest.mark.parametrize("raw", ["soon", None, 12.5, "", True, "inf", "nan"])
def test_interval_from_minutes_rejects_junk_naming_every_min(raw: object) -> None:
    with pytest.raises(ValueError, match="whole number"):
        Schedule.interval_from_minutes(raw)


def test_daily_from_hhmm_accepts_single_digit_hour() -> None:
    assert Schedule.daily_from_hhmm("9:05") == Schedule(
        kind="daily", at_hour=9, at_minute=5
    )


def test_next_due_at_from_now_collapses_a_missed_gap(eastern: None) -> None:
    """Re-arming from *now* skips every occurrence missed while the app was closed.

    The wake-once rule depends on the caller passing the current time; chaining
    from the stale deadline instead walks forward one day per call.
    """
    s = Schedule.daily_from_hhmm("09:00")
    missed_deadline = _local(2026, 5, 1, 9, 0)
    now = _local(2026, 5, 5, 10, 0)  # four days later, app was closed

    assert s.next_due_at(now) == _local(2026, 5, 6, 9, 0)
    # The trap this guards: chaining from the deadline is still in the past.
    assert s.next_due_at(missed_deadline) < now


@pytest.mark.parametrize("raw", ["9:3", "22:5", "09:5"])
def test_daily_from_hhmm_rejects_a_one_digit_minute(raw: str) -> None:
    """'9:3' means 09:30 to a user and 09:03 to strptime, so refuse it."""
    with pytest.raises(ValueError, match="24-hour time"):
        Schedule.daily_from_hhmm(raw)


@pytest.mark.parametrize("raw", ["1e3", "1_0", " 12 ", "+5"])
def test_interval_from_minutes_notation(raw: str) -> None:
    """Only plain integers; '1e3' must not silently arm a 16-hour interval."""
    if raw in ("1e3", "1_0"):
        with pytest.raises(ValueError, match="whole number"):
            Schedule.interval_from_minutes(raw)
    else:
        assert Schedule.interval_from_minutes(raw).interval_s == int(raw.strip()) * 60.0
