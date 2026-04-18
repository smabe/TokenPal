"""Data-only definitions for the idle-tool roller.

This module defines the dataclasses (`IdleToolContext`, `IdleToolRule`) and
the M1 rule catalog `M1_RULES`. It imports no orchestrator state so it can
be unit-tested in isolation.

A rule fires iff:
    rule.enabled_default (overridable via config) AND
    rule.predicate(context) AND
    (not rule.needs_web_fetches OR context.consent_web_fetches) AND
    now - last_fire_by_rule[rule.name] >= rule.cooldown_s

Framing strings are in-character hints appended to the riff prompt. Keep
them short — every word costs tokens on the hot path.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class IdleToolContext:
    """Snapshot passed to each rule's predicate."""

    now: datetime
    session_minutes: int
    first_session_of_day: bool
    active_readings: Mapping[str, Any]   # SenseReading, loose-typed to avoid cycle
    mood: str
    weather_summary: str
    time_since_last_comment_s: float
    consent_web_fetches: bool

    @property
    def hour(self) -> int:
        return self.now.hour

    @property
    def weekday(self) -> int:
        """Monday == 0, Sunday == 6."""
        return self.now.weekday()


Predicate = Callable[[IdleToolContext], bool]


@dataclass(frozen=True)
class IdleToolRule:
    name: str
    tool_name: str
    description: str
    weight: float
    cooldown_s: float
    predicate: Predicate
    framing: str
    needs_web_fetches: bool = True
    enabled_default: bool = True
    # M1 rules never set running_bit — that's M2 territory — but the field
    # exists now so the roller signature doesn't churn when M2 lands.
    running_bit: bool = False
    bit_decay_s: float = 0.0


# ---------------------------------------------------------------------------
# Predicates
# ---------------------------------------------------------------------------

def _evening_window(ctx: IdleToolContext) -> bool:
    return 21 <= ctx.hour < 24


def _morning_window(ctx: IdleToolContext) -> bool:
    return 6 <= ctx.hour < 11 and ctx.first_session_of_day


def _monday_morning(ctx: IdleToolContext) -> bool:
    return ctx.weekday == 0 and ctx.first_session_of_day and 6 <= ctx.hour < 11


def _weather_just_changed(ctx: IdleToolContext) -> bool:
    reading = ctx.active_readings.get("weather")
    if reading is None:
        return False
    return bool(getattr(reading, "changed_from", None))


def _deep_focus_reading(ctx: IdleToolContext) -> bool:
    for r in ctx.active_readings.values():
        summary = getattr(r, "summary", "")
        if isinstance(summary, str) and "Deep focus" in summary:
            return True
    return False


def _long_silence_mood_ok(ctx: IdleToolContext) -> bool:
    return ctx.time_since_last_comment_s > 900.0 and ctx.mood.lower() != "focused"


def _first_session_morning(ctx: IdleToolContext) -> bool:
    return ctx.first_session_of_day and 6 <= ctx.hour < 12


def _full_moon_late(ctx: IdleToolContext) -> bool:
    return _is_approximately_full_moon(ctx.now) and ctx.hour >= 22


def _settled_in_session(ctx: IdleToolContext) -> bool:
    return ctx.session_minutes > 15 and ctx.time_since_last_comment_s > 600.0


def _is_approximately_full_moon(when: datetime) -> bool:
    """Cheap full-moon check — within ±1.5 days of a known reference.

    Reference: 2026-03-03 18:37 UTC full moon. Synodic month = 29.5306 days.
    Good enough for an easter-egg trigger; we don't need astronomical
    precision here. `moon_phase` tool call provides the authoritative info.
    """
    reference = datetime(2026, 3, 3, 18, 37)
    delta_days = abs((when - reference).total_seconds()) / 86400.0
    phase = delta_days % 29.5306
    return phase < 1.5 or phase > (29.5306 - 1.5)


# ---------------------------------------------------------------------------
# M1 rule catalog
# ---------------------------------------------------------------------------

M1_RULES: tuple[IdleToolRule, ...] = (
    IdleToolRule(
        name="evening_moon",
        tool_name="moon_phase",
        description="Drops a lunar observation during the 9pm-midnight window.",
        weight=1.0,
        cooldown_s=24 * 3600,
        predicate=_evening_window,
        framing="Reference the moon phase in one line, in-character. No astronomy lecture.",
    ),
    IdleToolRule(
        name="morning_word",
        tool_name="word_of_the_day",
        description="Announces today's word on the first morning session.",
        weight=1.5,
        cooldown_s=18 * 3600,
        predicate=_morning_window,
        framing=(
            "You just learned today's word. Announce it in one line, in-character. "
            "Do not define it unless asked."
        ),
    ),
    IdleToolRule(
        name="monday_joke",
        tool_name="joke_of_the_day",
        description="Opens a Monday morning with a joke, told badly.",
        weight=1.0,
        cooldown_s=7 * 24 * 3600,
        predicate=_monday_morning,
        framing=(
            "Tell this joke badly, in your voice. Acknowledge that it's Monday once. "
            "One or two lines."
        ),
    ),
    IdleToolRule(
        name="weather_change",
        tool_name="weather_forecast_week",
        description="Riff on the week's forecast when the weather bucket just changed.",
        weight=1.2,
        cooldown_s=6 * 3600,
        predicate=_weather_just_changed,
        framing=(
            "Weather just shifted. Give a one-line read on the upcoming week, "
            "in-character. Do not list every day."
        ),
    ),
    IdleToolRule(
        name="long_focus_fact",
        tool_name="random_fact",
        description="Drops a random fact when the user has been in deep focus.",
        weight=0.8,
        cooldown_s=2 * 3600,
        predicate=_deep_focus_reading,
        framing=(
            "User is in deep focus. Drop this unrelated fact as a one-line aside, "
            "like you just remembered it. Do not interrupt their work further."
        ),
    ),
    IdleToolRule(
        name="deep_lull_trivia",
        tool_name="trivia_question",
        description="Tosses a trivia question after long silence.",
        weight=0.6,
        cooldown_s=2 * 3600,
        predicate=_long_silence_mood_ok,
        framing=(
            "Pose this trivia question in one line, in-character. Don't reveal "
            "the answer in the same line."
        ),
    ),
    IdleToolRule(
        name="on_this_day_opener",
        tool_name="on_this_day",
        description="Opens a new session with a historical this-day-in-history pick.",
        weight=1.3,
        cooldown_s=18 * 3600,
        predicate=_first_session_morning,
        framing=(
            "Pick ONE item from this-day-in-history and reference it in one line, "
            "in-character. Never a list."
        ),
    ),
    IdleToolRule(
        name="lunar_override",
        tool_name="moon_phase",
        description="Easter-egg: forced lunar callout on a full moon after 10pm.",
        weight=3.0,
        cooldown_s=24 * 3600,
        predicate=_full_moon_late,
        framing="It's a full moon and it's late. Lean into it. One line, in-character.",
    ),
    IdleToolRule(
        name="memory_recall",
        tool_name="memory_query",
        description=(
            "Offline floor — queries local memory.db for a habit stat, no network. "
            "Keeps the feature alive without web_fetches consent."
        ),
        weight=1.0,
        cooldown_s=3 * 3600,
        predicate=_settled_in_session,
        framing=(
            "You just recalled something from the user's own session history. "
            "Drop ONE observation about it, in-character. Use a striking number "
            "if there is one; otherwise skip the stat and just comment."
        ),
        needs_web_fetches=False,
    ),
)


def rule_by_name(name: str) -> IdleToolRule | None:
    for r in M1_RULES:
        if r.name == name:
            return r
    return None


def all_rule_names() -> tuple[str, ...]:
    return tuple(r.name for r in M1_RULES)
