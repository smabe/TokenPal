"""Tests for mood system — update_mood() heuristics and custom mood display."""

from __future__ import annotations

import time
from unittest.mock import patch

from tokenpal.brain.personality import Mood, PersonalityEngine, _ENUM_TO_ROLE
from tokenpal.tools.voice_profile import VoiceProfile


def _make_engine() -> PersonalityEngine:
    return PersonalityEngine("You are a test bot.")


def _make_custom_mood_profile() -> VoiceProfile:
    return VoiceProfile(
        character="BMO",
        source="test",
        created="2026-01-01",
        lines=[f"Line {i}" for i in range(15)],
        persona="A little game console.",
        mood_prompts={
            "default": "Your current mood: PLAYFUL. BMO is feeling playful boyee!",
            "sleepy": "Your current mood: DROWSY. BMO cannot self-snooze.",
            "bored": "Your current mood: BLAH. Nothing is happening boy.",
            "hyper": "Your current mood: TURBO. Everything is awesome!",
            "impressed": "Your current mood: WHOA. That was algebraic.",
            "concerned": "Your current mood: WORRIED. Are you okay boy?",
        },
        mood_roles={
            "default": "PLAYFUL",
            "sleepy": "DROWSY",
            "bored": "BLAH",
            "hyper": "TURBO",
            "impressed": "WHOA",
            "concerned": "WORRIED",
        },
        default_mood="PLAYFUL",
    )


def _make_old_style_profile() -> VoiceProfile:
    """Old profile with legacy mood_prompts keys (mood names, not roles)."""
    return VoiceProfile(
        character="Bender",
        source="test",
        created="2026-01-01",
        lines=[f"Line {i}" for i in range(15)],
        persona="A foul-mouthed robot.",
        mood_prompts={
            "snarky": "Your current mood: SNARKY. Bite my shiny metal observation.",
            "bored": "Your current mood: BORED. This is duller than Calculon reruns.",
        },
    )


# ---------------------------------------------------------------
# ENUM_TO_ROLE mapping
# ---------------------------------------------------------------

def test_enum_to_role_covers_all_moods():
    """Every Mood enum member has a role mapping."""
    for mood in Mood:
        assert mood in _ENUM_TO_ROLE


# ---------------------------------------------------------------
# update_mood() heuristic triggers (default TokenPal, no voice)
# ---------------------------------------------------------------

def test_update_mood_default_is_snarky():
    engine = _make_engine()
    assert engine._mood == Mood.SNARKY


def test_update_mood_sleepy_early_morning():
    engine = _make_engine()
    engine._context_unchanged_count = 5
    with patch("tokenpal.brain.personality.datetime") as mock_dt:
        mock_dt.now.return_value = type("FakeDT", (), {"hour": 6})()
        engine.update_mood("App: Terminal")
    assert engine._mood == Mood.SLEEPY


def test_update_mood_concerned_late_night():
    engine = _make_engine()
    with patch("tokenpal.brain.personality.datetime") as mock_dt:
        mock_dt.now.return_value = type("FakeDT", (), {"hour": 3})()
        engine.update_mood("App: Terminal")
    assert engine._mood == Mood.CONCERNED


def test_update_mood_bored_stale_context():
    engine = _make_engine()
    engine._context_unchanged_count = 12
    engine._last_mood_app = "Terminal"
    engine._last_seen_app = "Terminal"
    with patch("tokenpal.brain.personality.datetime") as mock_dt:
        mock_dt.now.return_value = type("FakeDT", (), {"hour": 14})()
        engine.update_mood("App: Terminal")
    assert engine._mood == Mood.BORED


def test_update_mood_hyper_rapid_switching():
    engine = _make_engine()
    engine._context_unchanged_count = 0
    engine._last_mood_app = "OtherApp"
    engine._last_seen_app = "Terminal"
    engine._mood_since = time.monotonic() - 40  # >30s in current mood
    with patch("tokenpal.brain.personality.datetime") as mock_dt:
        mock_dt.now.return_value = type("FakeDT", (), {"hour": 14})()
        engine.update_mood("App: Terminal")
    assert engine._mood == Mood.HYPER


def test_update_mood_impressed_by_keywords():
    engine = _make_engine()
    engine._context_unchanged_count = 0
    engine._last_mood_app = "OtherApp"
    engine._last_seen_app = "Terminal"
    with patch("tokenpal.brain.personality.datetime") as mock_dt:
        mock_dt.now.return_value = type("FakeDT", (), {"hour": 14})()
        engine.update_mood("App: Terminal, commit pushed")
    assert engine._mood == Mood.IMPRESSED


def test_update_mood_reverts_to_snarky():
    engine = _make_engine()
    engine._mood = Mood.BORED
    engine._mood_since = time.monotonic() - 130  # >120s
    engine._context_unchanged_count = 5  # not stale enough for BORED, not 0 for HYPER
    engine._last_mood_app = "Terminal"
    engine._last_seen_app = "Terminal"
    with patch("tokenpal.brain.personality.datetime") as mock_dt:
        mock_dt.now.return_value = type("FakeDT", (), {"hour": 14})()
        engine.update_mood("App: Terminal")
    assert engine._mood == Mood.SNARKY


# ---------------------------------------------------------------
# Custom mood display names
# ---------------------------------------------------------------

def test_custom_mood_default_display():
    engine = _make_engine()
    engine.set_voice(_make_custom_mood_profile())
    assert engine.mood == "playful"


def test_custom_mood_display_after_transition():
    engine = _make_engine()
    engine.set_voice(_make_custom_mood_profile())
    engine._context_unchanged_count = 12
    engine._last_mood_app = "Terminal"
    engine._last_seen_app = "Terminal"
    with patch("tokenpal.brain.personality.datetime") as mock_dt:
        mock_dt.now.return_value = type("FakeDT", (), {"hour": 14})()
        engine.update_mood("App: Terminal")
    assert engine._mood == Mood.BORED
    assert engine.mood == "blah"


def test_custom_mood_no_voice_returns_enum_value():
    engine = _make_engine()
    assert engine.mood == "snarky"


# ---------------------------------------------------------------
# _mood_line() 3-tier fallback
# ---------------------------------------------------------------

def test_mood_line_tier1_role_keyed():
    engine = _make_engine()
    engine.set_voice(_make_custom_mood_profile())
    line = engine._mood_line()
    assert "PLAYFUL" in line


def test_mood_line_tier2_legacy_key():
    engine = _make_engine()
    engine.set_voice(_make_old_style_profile())
    line = engine._mood_line()
    assert "shiny metal" in line


def test_mood_line_tier3_hardcoded_fallback():
    engine = _make_engine()
    line = engine._mood_line()
    assert "SNARKY" in line


def test_mood_line_custom_after_transition():
    engine = _make_engine()
    engine.set_voice(_make_custom_mood_profile())
    engine._mood = Mood.BORED
    line = engine._mood_line()
    assert "BLAH" in line


# ---------------------------------------------------------------
# Voice hot-swap and mood state
# ---------------------------------------------------------------

def test_hot_swap_to_custom_voice():
    engine = _make_engine()
    engine.set_voice(_make_custom_mood_profile())
    assert engine.mood == "playful"
    assert "PLAYFUL" in engine._mood_line()


def test_hot_swap_to_none_restores_defaults():
    engine = _make_engine()
    engine.set_voice(_make_custom_mood_profile())
    assert engine.mood == "playful"
    engine.set_voice(None)
    assert engine.mood == "snarky"
    assert "SNARKY" in engine._mood_line()


def test_hot_swap_custom_to_custom():
    engine = _make_engine()
    engine.set_voice(_make_custom_mood_profile())
    assert engine.mood == "playful"

    other = VoiceProfile(
        character="Bender",
        source="test",
        created="2026-01-01",
        lines=[f"Line {i}" for i in range(15)],
        mood_roles={"default": "SMUG", "bored": "DISGUSTED"},
        mood_prompts={"default": "Your current mood: SMUG.", "bored": "Your current mood: DISGUSTED."},
        default_mood="SMUG",
    )
    engine.set_voice(other)
    assert engine.mood == "smug"


# ---------------------------------------------------------------
# Late-night override
# ---------------------------------------------------------------

def test_late_night_override_with_custom_moods():
    engine = _make_engine()
    engine.set_voice(_make_custom_mood_profile())
    with patch("tokenpal.brain.personality.datetime") as mock_dt:
        mock_dt.now.return_value = type("FakeDT", (), {"hour": 3, "weekday": lambda: 1})()
        prompt = engine.build_prompt("App: Terminal", memory_lines=[])
    assert "MILDLY SUPPORTIVE" in prompt


# ---------------------------------------------------------------
# Prompt builders include custom mood
# ---------------------------------------------------------------

def test_build_prompt_contains_custom_mood():
    engine = _make_engine()
    engine.set_voice(_make_custom_mood_profile())
    with patch("tokenpal.brain.personality.datetime") as mock_dt:
        mock_dt.now.return_value = type("FakeDT", (), {"hour": 14, "weekday": lambda: 1})()
        prompt = engine.build_prompt("App: Terminal", memory_lines=[])
    assert "PLAYFUL" in prompt


def test_freeform_prompt_contains_custom_mood():
    engine = _make_engine()
    engine.set_voice(_make_custom_mood_profile())
    prompt = engine.build_freeform_prompt()
    assert "PLAYFUL" in prompt


def test_conversation_prompt_contains_custom_mood():
    engine = _make_engine()
    engine.set_voice(_make_custom_mood_profile())
    prompt = engine.build_conversation_prompt("hello", "App: Terminal")
    assert "PLAYFUL" in prompt
