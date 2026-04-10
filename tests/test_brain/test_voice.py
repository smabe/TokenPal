"""Tests for voice hot-swapping on PersonalityEngine."""

from __future__ import annotations

from tokenpal.brain.personality import PersonalityEngine
from tokenpal.tools.voice_profile import VoiceProfile


def _make_engine() -> PersonalityEngine:
    return PersonalityEngine("You are a test bot.")


def _make_profile() -> VoiceProfile:
    return VoiceProfile(
        character="Bender",
        source="test",
        created="2026-01-01",
        lines=[f"Line {i}" for i in range(15)],
        persona="A foul-mouthed robot who bends things.",
        greetings=["Bite my shiny metal..."],
        offline_quips=["I was sleeping."],
    )


def test_set_voice_updates_name():
    engine = _make_engine()
    assert engine.voice_name == ""

    engine.set_voice(_make_profile())
    assert engine.voice_name == "Bender"


def test_set_voice_updates_example_pool():
    engine = _make_engine()
    original_pool = list(engine._example_pool)

    engine.set_voice(_make_profile())
    assert engine._example_pool != original_pool
    assert any("Line" in ex for ex in engine._example_pool)


def test_set_voice_none_resets():
    engine = _make_engine()
    engine.set_voice(_make_profile())
    assert engine.voice_name == "Bender"

    engine.set_voice(None)
    assert engine.voice_name == ""
    assert not any("Line" in ex for ex in engine._example_pool)


def test_set_voice_updates_greetings():
    engine = _make_engine()
    engine.set_voice(_make_profile())
    assert "Bite my shiny metal..." in engine._voice_greetings


def test_set_voice_updates_persona_in_prompt():
    engine = _make_engine()
    engine.set_voice(_make_profile())
    prompt = engine.build_conversation_prompt("hello", "App: Terminal")
    assert "foul-mouthed robot" in prompt


def test_voice_name_property_default():
    engine = _make_engine()
    assert engine.voice_name == ""


def test_mood_property():
    engine = _make_engine()
    assert engine.mood == "snarky"
