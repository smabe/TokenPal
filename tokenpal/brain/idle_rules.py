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
    # When True, the fire calls PersonalityEngine.add_running_bit instead of
    # (or in addition to) emitting a one-shot riff. `framing` supplies the
    # soft system-prompt instruction that rides along for `bit_decay_s`
    # seconds. `opener_framing`, if set, also emits a one-line announcement
    # right now so the user hears when the bit was registered.
    running_bit: bool = False
    bit_decay_s: float = 0.0
    opener_framing: str = ""
    # Chain rules invoke extra tools alongside tool_name. Results land in
    # IdleFireResult.extra_outputs and the orchestrator weaves them into a
    # single multi-tool riff (used by morning_monologue).
    extra_tool_names: tuple[str, ...] = ()


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


def _midday_quiet(ctx: IdleToolContext) -> bool:
    """Catch a mid-workday lull — same settled bar, noonish window."""
    return (
        11 <= ctx.hour < 15
        and ctx.session_minutes > 10
        and ctx.time_since_last_comment_s > 300.0
    )


def _morning_radio_window(ctx: IdleToolContext) -> bool:
    return ctx.first_session_of_day and 6 <= ctx.hour < 10


def _friday_afternoon_lull(ctx: IdleToolContext) -> bool:
    """Friday 15:00-18:00, settled in a session, long-ish quiet stretch.

    Bar is a little higher than _midday_quiet — Friday-wrap is meant to
    feel like the end of the workweek, not a regular afternoon chime.
    """
    return (
        ctx.weekday == 4
        and 15 <= ctx.hour < 18
        and ctx.session_minutes > 20
        and ctx.time_since_last_comment_s > 420.0
    )


def _coffee_break_window(ctx: IdleToolContext) -> bool:
    """Mid-morning 10-12, clearly NOT the first session of the day, settled.

    first_session_of_day=False means `morning_monologue` already ran (or
    the user skipped it), so coffee_break can legitimately double up on
    a word + trivia riff without stomping the radio-broadcast rule.
    """
    return (
        10 <= ctx.hour < 12
        and not ctx.first_session_of_day
        and ctx.session_minutes > 10
        and ctx.time_since_last_comment_s > 360.0
    )


def _late_night_host_window(ctx: IdleToolContext) -> bool:
    """23:00-01:59, long-silence bar, mood isn't 'focused'.

    Mimics the existing _long_silence_mood_ok feel, but time-windowed so
    a 23:30 user on a Friday night with nothing else going on hears the
    late-night host pipe up instead of yet another trivia question.
    """
    hour = ctx.hour
    if not (hour >= 23 or hour < 2):
        return False
    if ctx.mood.lower() == "focused":
        return False
    return ctx.time_since_last_comment_s > 600.0


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
        description=(
            "Announces today's word on the first morning session, then rides "
            "along for 8h as a soft callback."
        ),
        weight=1.5,
        cooldown_s=18 * 3600,
        predicate=_morning_window,
        framing=(
            "Today's word: {output}. Slip it in naturally when a moment fits; "
            "once is enough. Never re-define unless the user asks."
        ),
        running_bit=True,
        bit_decay_s=8 * 3600,
        opener_framing=(
            "You just learned today's word. Announce it in one line, "
            "in-character. Do not define it unless asked."
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
        name="todays_joke_bit",
        tool_name="joke_of_the_day",
        description=(
            "Heard a joke earlier today — referenceable for 4h, callback-only."
        ),
        weight=0.8,
        cooldown_s=12 * 3600,
        predicate=_midday_quiet,
        framing=(
            "You heard a joke today: {output}. Reference it as a callback "
            "if a moment comes up. Never re-tell outright."
        ),
        running_bit=True,
        bit_decay_s=4 * 3600,
        opener_framing="",
    ),
    IdleToolRule(
        name="morning_monologue",
        tool_name="weather_forecast_week",
        description=(
            "First-session morning radio broadcast — chains forecast, "
            "sunrise/sunset, and this-day-in-history into one riff."
        ),
        weight=1.4,
        cooldown_s=24 * 3600,
        predicate=_morning_radio_window,
        framing=(
            "You're doing your 30-second morning radio broadcast. Weave the "
            "forecast, sunrise, and one this-day-in-history item into a "
            "single short riff, in-character. Do not list every detail."
        ),
        extra_tool_names=("sunrise_sunset", "on_this_day"),
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
    IdleToolRule(
        name="friday_wrap",
        tool_name="joke_of_the_day",
        description=(
            "Friday-afternoon wrap riff — chains a joke, a random fact, and "
            "a this-day-in-history item into one end-of-week send-off."
        ),
        weight=1.3,
        cooldown_s=7 * 24 * 3600,
        predicate=_friday_afternoon_lull,
        framing=(
            "The workweek is winding down. Weave the joke, the fact, and one "
            "this-day-in-history item into a single short Friday-wrap riff, "
            "in-character. Do not tell the joke verbatim; paraphrase or react "
            "to it. One paragraph, not a list."
        ),
        extra_tool_names=("random_fact", "on_this_day"),
    ),
    IdleToolRule(
        name="coffee_break",
        tool_name="word_of_the_day",
        description=(
            "Mid-morning second-session riff — word of the day plus a trivia "
            "question as a coffee-break aside."
        ),
        weight=1.0,
        cooldown_s=12 * 3600,
        predicate=_coffee_break_window,
        framing=(
            "You're between deep-work blocks — the coffee-break moment. "
            "Use today's word in a natural sentence (do NOT define it), then "
            "pose the trivia question as a casual aside. One short paragraph. "
            "Do not reveal the trivia answer."
        ),
        extra_tool_names=("trivia_question",),
    ),
    IdleToolRule(
        name="late_night_host",
        tool_name="trivia_question",
        description=(
            "Late-night riff — trivia + random fact + moon phase, delivered "
            "in a tonight-show-monologue voice."
        ),
        weight=1.2,
        cooldown_s=24 * 3600,
        predicate=_late_night_host_window,
        framing=(
            "You're doing the late-night-host monologue. Lean into the hour. "
            "Weave the trivia, the fact, and the moon phase into one short "
            "monologue, in-character. Don't reveal the trivia answer. "
            "One paragraph, not a list."
        ),
        extra_tool_names=("random_fact", "moon_phase"),
    ),
)


def rule_by_name(name: str) -> IdleToolRule | None:
    for r in M1_RULES:
        if r.name == name:
            return r
    return None


def all_rule_names() -> tuple[str, ...]:
    return tuple(r.name for r in M1_RULES)
