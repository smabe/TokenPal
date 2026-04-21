"""Tests for the brain's mood-change callback that drives the overlay's
mood-aware frame swap."""

from __future__ import annotations

from unittest.mock import MagicMock

from tokenpal.brain.orchestrator import Brain
from tokenpal.brain.personality import Mood, PersonalityEngine


def _bare_brain(
    personality: PersonalityEngine,
    mood_callback=None,
) -> Brain:
    obj = Brain.__new__(Brain)
    obj._personality = personality
    obj._mood_callback = mood_callback
    obj._last_mood_role = personality.mood_role
    return obj


def test_push_mood_noop_when_callback_missing() -> None:
    eng = PersonalityEngine("test")
    brain = _bare_brain(eng, mood_callback=None)
    eng._mood = Mood.SLEEPY
    brain._push_mood_if_changed()  # must not raise.


def test_push_mood_fires_on_role_change() -> None:
    eng = PersonalityEngine("test")
    cb = MagicMock()
    brain = _bare_brain(eng, mood_callback=cb)

    # Initial state is SNARKY ("default"); no callback yet.
    brain._push_mood_if_changed()
    cb.assert_not_called()

    # Transition to SLEEPY triggers the callback with "sleepy".
    eng._mood = Mood.SLEEPY
    brain._push_mood_if_changed()
    cb.assert_called_once_with("sleepy")


def test_push_mood_only_fires_once_per_role_transition() -> None:
    eng = PersonalityEngine("test")
    cb = MagicMock()
    brain = _bare_brain(eng, mood_callback=cb)

    eng._mood = Mood.BORED
    brain._push_mood_if_changed()
    brain._push_mood_if_changed()
    brain._push_mood_if_changed()
    cb.assert_called_once_with("bored")

    # Back to default role fires exactly once.
    eng._mood = Mood.SNARKY
    brain._push_mood_if_changed()
    brain._push_mood_if_changed()
    assert cb.call_count == 2
    cb.assert_called_with("default")


def test_push_mood_swallows_callback_exception() -> None:
    """Callback errors must not kill the brain loop."""
    eng = PersonalityEngine("test")

    def _bad(_role: str) -> None:
        raise RuntimeError("overlay gone")

    brain = _bare_brain(eng, mood_callback=_bad)
    eng._mood = Mood.HYPER
    brain._push_mood_if_changed()  # must not raise.
