"""Tests for the suppression cooldown + forced-silence escalation.

Without this guard a buddy stuck in a template-lock loop burns an LLM call
every brain-loop tick (3s) because `_last_comment_time` is only advanced
on successful emits. Observed live: 3,250 suppressed generations in a
single overnight session, zero idle-tool fires because the gate never
picked silence.
"""

from __future__ import annotations

import time
from collections import deque

from tokenpal.brain.orchestrator import (
    _FORCED_SILENCE_AFTER_SUPPRESSIONS,
    _FORCED_SILENCE_DURATION,
    Brain,
)


def _make_harness() -> Brain:
    """Bare orchestrator with just the state the suppression path touches."""
    obj = Brain.__new__(Brain)
    obj._recent_outputs = deque(maxlen=10)
    obj._consecutive_comments = 0
    obj._suppressed_streak = 0
    obj._forced_silence_until = 0.0
    obj._last_comment_time = 0.0
    obj._comment_timestamps = deque()
    return obj


def test_suppression_advances_last_comment_time() -> None:
    """Cooldown timer must advance so the gate doesn't re-fire next tick."""
    o = _make_harness()
    before = o._last_comment_time
    o._handle_suppressed_output("test")
    assert o._last_comment_time > before


def test_suppression_resets_consecutive_comments() -> None:
    """Suppression is not a successful comment; the consecutive counter clears."""
    o = _make_harness()
    o._consecutive_comments = 2
    o._handle_suppressed_output("test")
    assert o._consecutive_comments == 0


def test_streak_increments() -> None:
    o = _make_harness()
    for _ in range(3):
        o._handle_suppressed_output("test")
    assert o._suppressed_streak == 3


def test_forced_silence_trips_after_threshold() -> None:
    """Hitting the suppression cap installs a forced-silence window."""
    o = _make_harness()
    for _ in range(_FORCED_SILENCE_AFTER_SUPPRESSIONS):
        o._handle_suppressed_output("test")
    now = time.monotonic()
    assert o._forced_silence_until > now
    # Duration should be close to the forced-silence constant.
    remaining = o._forced_silence_until - now
    assert abs(remaining - _FORCED_SILENCE_DURATION) < 1.0
    # Streak resets so next batch needs to cross the threshold again.
    assert o._suppressed_streak == 0


def test_emit_comment_clears_suppression_streak() -> None:
    """A successful emit forgives the streak so the user gets comments again."""
    o = _make_harness()
    o._suppressed_streak = 2
    # Stub out the collaborators that _emit_comment touches.
    o._personality = type("P", (), {"record_comment": lambda self, _t: None})()
    o._ui_callback = lambda _t: None
    o._emit_comment("hello there")
    assert o._suppressed_streak == 0


def test_threshold_constant_is_sane() -> None:
    assert 3 <= _FORCED_SILENCE_AFTER_SUPPRESSIONS <= 10
