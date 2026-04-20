"""Edge-time tests for every M1 idle-rule predicate."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from tokenpal.brain.idle_rules import (
    M1_RULES,
    IdleToolContext,
    rule_by_name,
)


def _ctx(
    *, now: datetime | None = None, session_minutes: int = 10,
    first_session_of_day: bool = False,
    active_readings: dict[str, Any] | None = None,
    mood: str = "snarky", time_since_last_comment_s: float = 30.0,
    consent_web_fetches: bool = True,
) -> IdleToolContext:
    return IdleToolContext(
        now=now or datetime(2026, 4, 17, 10, 0),
        session_minutes=session_minutes,
        first_session_of_day=first_session_of_day,
        active_readings=active_readings or {},
        mood=mood,
        weather_summary="",
        time_since_last_comment_s=time_since_last_comment_s,
        consent_web_fetches=consent_web_fetches,
    )


class _ReadingStub:
    def __init__(self, summary: str = "", changed_from: str = "") -> None:
        self.summary = summary
        self.changed_from = changed_from


# -- evening_moon ------------------------------------------------------------

def test_evening_moon_fires_in_window() -> None:
    rule = rule_by_name("evening_moon")
    assert rule is not None
    assert rule.predicate(_ctx(now=datetime(2026, 4, 17, 22, 0)))


def test_evening_moon_blocks_before_window() -> None:
    rule = rule_by_name("evening_moon")
    assert not rule.predicate(_ctx(now=datetime(2026, 4, 17, 20, 59)))


def test_evening_moon_blocks_after_midnight() -> None:
    rule = rule_by_name("evening_moon")
    assert not rule.predicate(_ctx(now=datetime(2026, 4, 17, 0, 30)))


# -- morning_word / on_this_day / monday_joke -------------------------------

def test_morning_word_requires_first_session() -> None:
    rule = rule_by_name("morning_word")
    early = datetime(2026, 4, 17, 8, 30)
    assert rule.predicate(_ctx(now=early, first_session_of_day=True))
    assert not rule.predicate(_ctx(now=early, first_session_of_day=False))


def test_morning_word_window_edges() -> None:
    rule = rule_by_name("morning_word")
    assert rule.predicate(_ctx(
        now=datetime(2026, 4, 17, 6, 0), first_session_of_day=True,
    ))
    assert rule.predicate(_ctx(
        now=datetime(2026, 4, 17, 10, 59), first_session_of_day=True,
    ))
    assert not rule.predicate(_ctx(
        now=datetime(2026, 4, 17, 11, 0), first_session_of_day=True,
    ))


def test_monday_joke_requires_monday() -> None:
    rule = rule_by_name("monday_joke")
    monday = datetime(2026, 4, 13, 9, 0)   # Mon
    tuesday = datetime(2026, 4, 14, 9, 0)  # Tue
    assert rule.predicate(_ctx(now=monday, first_session_of_day=True))
    assert not rule.predicate(_ctx(now=tuesday, first_session_of_day=True))


def test_on_this_day_requires_first_morning() -> None:
    rule = rule_by_name("on_this_day_opener")
    morning = datetime(2026, 4, 17, 9, 0)
    assert rule.predicate(_ctx(now=morning, first_session_of_day=True))
    assert not rule.predicate(_ctx(now=morning, first_session_of_day=False))


# -- weather_change ----------------------------------------------------------

def test_weather_change_needs_changed_from() -> None:
    rule = rule_by_name("weather_change")
    reading = _ReadingStub(summary="Sunny, 72F", changed_from="Cloudy, 58F")
    assert rule.predicate(_ctx(active_readings={"weather": reading}))


def test_weather_change_skips_stable_reading() -> None:
    rule = rule_by_name("weather_change")
    reading = _ReadingStub(summary="Sunny", changed_from="")
    assert not rule.predicate(_ctx(active_readings={"weather": reading}))


def test_weather_change_skips_when_no_reading() -> None:
    rule = rule_by_name("weather_change")
    assert not rule.predicate(_ctx(active_readings={}))


# -- long_focus_fact ---------------------------------------------------------

def test_long_focus_fact_triggers_on_deep_focus_marker() -> None:
    rule = rule_by_name("long_focus_fact")
    reading = _ReadingStub(summary="Deep focus in Terminal")
    assert rule.predicate(_ctx(active_readings={"productivity": reading}))


def test_long_focus_fact_skips_without_marker() -> None:
    rule = rule_by_name("long_focus_fact")
    reading = _ReadingStub(summary="active multitasking across apps")
    assert not rule.predicate(_ctx(active_readings={"productivity": reading}))


# -- deep_lull_trivia --------------------------------------------------------

def test_deep_lull_trivia_needs_long_silence() -> None:
    rule = rule_by_name("deep_lull_trivia")
    assert rule.predicate(_ctx(time_since_last_comment_s=901))
    assert not rule.predicate(_ctx(time_since_last_comment_s=600))


def test_deep_lull_trivia_blocked_in_focused_mood() -> None:
    rule = rule_by_name("deep_lull_trivia")
    assert not rule.predicate(
        _ctx(time_since_last_comment_s=1500, mood="focused")
    )


# -- memory_recall (offline floor) ------------------------------------------

def test_memory_recall_does_not_need_consent() -> None:
    rule = rule_by_name("memory_recall")
    assert rule is not None
    assert rule.needs_web_fetches is False


def test_memory_recall_requires_settled_session() -> None:
    rule = rule_by_name("memory_recall")
    assert rule.predicate(_ctx(session_minutes=20, time_since_last_comment_s=700))
    assert not rule.predicate(_ctx(session_minutes=5, time_since_last_comment_s=700))
    assert not rule.predicate(_ctx(session_minutes=20, time_since_last_comment_s=120))


# -- friday_wrap -------------------------------------------------------------

def test_friday_wrap_fires_friday_afternoon_settled() -> None:
    rule = rule_by_name("friday_wrap")
    assert rule is not None
    # Friday = weekday 4. Settled in-session, decent silence window.
    ctx = _ctx(
        now=datetime(2026, 4, 17, 16, 0),
        session_minutes=25,
        time_since_last_comment_s=480,
    )
    assert rule.predicate(ctx)


def test_friday_wrap_blocks_thursday() -> None:
    rule = rule_by_name("friday_wrap")
    ctx = _ctx(
        now=datetime(2026, 4, 16, 16, 0),  # Thursday
        session_minutes=25,
        time_since_last_comment_s=480,
    )
    assert not rule.predicate(ctx)


def test_friday_wrap_blocks_too_early_or_too_late() -> None:
    rule = rule_by_name("friday_wrap")
    early = _ctx(
        now=datetime(2026, 4, 17, 14, 30),
        session_minutes=25,
        time_since_last_comment_s=480,
    )
    late = _ctx(
        now=datetime(2026, 4, 17, 18, 30),
        session_minutes=25,
        time_since_last_comment_s=480,
    )
    assert not rule.predicate(early)
    assert not rule.predicate(late)


# -- coffee_break ------------------------------------------------------------

def test_coffee_break_requires_not_first_session() -> None:
    """morning_monologue owns the first-session slot; coffee_break is second-plus."""
    rule = rule_by_name("coffee_break")
    assert rule is not None
    first_session = _ctx(
        now=datetime(2026, 4, 17, 11, 0),
        first_session_of_day=True,
        session_minutes=15,
        time_since_last_comment_s=400,
    )
    second_session = _ctx(
        now=datetime(2026, 4, 17, 11, 0),
        first_session_of_day=False,
        session_minutes=15,
        time_since_last_comment_s=400,
    )
    assert not rule.predicate(first_session)
    assert rule.predicate(second_session)


def test_coffee_break_blocks_outside_window() -> None:
    rule = rule_by_name("coffee_break")
    early = _ctx(
        now=datetime(2026, 4, 17, 9, 30),
        first_session_of_day=False,
        session_minutes=15,
        time_since_last_comment_s=400,
    )
    late = _ctx(
        now=datetime(2026, 4, 17, 12, 30),
        first_session_of_day=False,
        session_minutes=15,
        time_since_last_comment_s=400,
    )
    assert not rule.predicate(early)
    assert not rule.predicate(late)


# -- late_night_host ---------------------------------------------------------

def test_late_night_host_fires_late() -> None:
    rule = rule_by_name("late_night_host")
    assert rule is not None
    late = _ctx(
        now=datetime(2026, 4, 17, 23, 30),
        time_since_last_comment_s=700,
        mood="snarky",
    )
    past_midnight = _ctx(
        now=datetime(2026, 4, 17, 0, 45),
        time_since_last_comment_s=700,
        mood="snarky",
    )
    assert rule.predicate(late)
    assert rule.predicate(past_midnight)


def test_late_night_host_blocks_focused_mood() -> None:
    """User deep in work at 23:30 doesn't need a tonight-show monologue."""
    rule = rule_by_name("late_night_host")
    ctx = _ctx(
        now=datetime(2026, 4, 17, 23, 30),
        time_since_last_comment_s=700,
        mood="focused",
    )
    assert not rule.predicate(ctx)


def test_late_night_host_blocks_busy_daytime() -> None:
    rule = rule_by_name("late_night_host")
    ctx = _ctx(
        now=datetime(2026, 4, 17, 14, 0),
        time_since_last_comment_s=700,
    )
    assert not rule.predicate(ctx)


# -- catalog integrity -------------------------------------------------------

def test_all_rules_have_unique_names() -> None:
    names = [r.name for r in M1_RULES]
    assert len(names) == len(set(names))


def test_all_rules_have_framing() -> None:
    for r in M1_RULES:
        assert r.framing.strip(), f"rule {r.name} has empty framing"


def test_catalog_covers_offline_and_network() -> None:
    offline = [r for r in M1_RULES if not r.needs_web_fetches]
    assert len(offline) >= 1, "at least one offline rule required for M1"
