"""Tests for fine-tuned model prompt behavior in PersonalityEngine."""

from __future__ import annotations

from tokenpal.brain.personality import PersonalityEngine
from tokenpal.tools.voice_profile import VoiceProfile


def _make_engine() -> PersonalityEngine:
    return PersonalityEngine("You are a test bot.")


def _make_finetuned_profile() -> VoiceProfile:
    return VoiceProfile(
        character="Mordecai",
        source="regularshow",
        created="2026-01-01",
        lines=[f"Dude, line {i}." for i in range(20)],
        persona="A blue jay who says dude.",
        finetuned_model="tokenpal-mordecai",
        finetuned_base="gemma-2-9b",
        finetuned_date="2026-04-09T12:00:00",
    )


def _make_regular_profile() -> VoiceProfile:
    return VoiceProfile(
        character="Bender",
        source="test",
        created="2026-01-01",
        lines=[f"Line {i}" for i in range(20)],
        persona="A robot.",
    )


def test_is_finetuned_with_finetuned_voice():
    engine = _make_engine()
    engine.set_voice(_make_finetuned_profile())
    assert engine.is_finetuned is True


def test_is_finetuned_with_regular_voice():
    engine = _make_engine()
    engine.set_voice(_make_regular_profile())
    assert engine.is_finetuned is False


def test_is_finetuned_default():
    engine = _make_engine()
    assert engine.is_finetuned is False


def test_finetuned_model_property():
    engine = _make_engine()
    engine.set_voice(_make_finetuned_profile())
    assert engine.finetuned_model == "tokenpal-mordecai"


def test_finetuned_model_empty_default():
    engine = _make_engine()
    assert engine.finetuned_model == ""


def test_finetuned_prompt_has_no_examples():
    engine = _make_engine()
    engine.set_voice(_make_finetuned_profile())
    prompt = engine.build_prompt("App: Terminal")
    # The fine-tuned template should NOT have "Examples:" section
    assert "Examples:" not in prompt


def test_regular_prompt_has_examples():
    engine = _make_engine()
    engine.set_voice(_make_regular_profile())
    prompt = engine.build_prompt("App: Terminal")
    assert "Examples:" in prompt


def test_finetuned_prompt_has_context():
    engine = _make_engine()
    engine.set_voice(_make_finetuned_profile())
    prompt = engine.build_prompt("App: VS Code")
    assert "VS Code" in prompt


def test_finetuned_prompt_has_mood():
    engine = _make_engine()
    engine.set_voice(_make_finetuned_profile())
    prompt = engine.build_prompt("App: Terminal")
    assert "mood" in prompt.lower() or "SNARKY" in prompt


def test_finetuned_prompt_has_no_voice_block():
    engine = _make_engine()
    engine.set_voice(_make_finetuned_profile())
    prompt = engine.build_prompt("App: Terminal")
    # Fine-tuned prompt shouldn't have "Your voice:" — the model IS the voice
    assert "Your voice:" not in prompt


def test_finetuned_freeform_prompt():
    engine = _make_engine()
    engine.set_voice(_make_finetuned_profile())
    prompt = engine.build_freeform_prompt()
    assert "Examples" not in prompt
    assert "random thought" in prompt.lower() or "in character" in prompt.lower()


def test_finetuned_conversation_prompt():
    engine = _make_engine()
    engine.set_voice(_make_finetuned_profile())
    prompt = engine.build_conversation_prompt("hello", "App: Terminal")
    assert "hello" in prompt
    assert "Terminal" in prompt
    # No voice block in fine-tuned conversation prompt
    assert "Your voice:" not in prompt


def test_fallback_to_prompt_when_no_finetune():
    engine = _make_engine()
    engine.set_voice(_make_regular_profile())
    prompt = engine.build_prompt("App: Terminal")
    # Regular voices should use the full template with examples
    assert "Examples:" in prompt


def test_reset_clears_finetuned():
    engine = _make_engine()
    engine.set_voice(_make_finetuned_profile())
    assert engine.is_finetuned is True

    engine.set_voice(None)
    assert engine.is_finetuned is False
    assert engine.finetuned_model == ""
