"""Tests for the conversational prompt path."""

from __future__ import annotations

from tokenpal.brain.personality import PersonalityEngine


def _make_engine() -> PersonalityEngine:
    return PersonalityEngine("You are a test bot.")


def test_build_conversation_prompt_contains_user_message():
    engine = _make_engine()
    prompt = engine.build_conversation_prompt("hello there", "App: VS Code")
    assert 'User says: "hello there"' in prompt
    assert "VS Code" in prompt


def test_build_conversation_prompt_contains_mood():
    engine = _make_engine()
    prompt = engine.build_conversation_prompt("test", "App: Chrome")
    assert "mood" in prompt.lower()


def test_build_conversation_prompt_no_silent_instruction():
    engine = _make_engine()
    prompt = engine.build_conversation_prompt("test", "")
    assert "[SILENT]" not in prompt


def test_filter_conversation_allows_short_responses():
    engine = _make_engine()
    # "No." is 3 chars — below observation min (15) but above conversation min (5)
    assert engine.filter_conversation_response("Never.") == "Never."


def test_filter_conversation_allows_longer_responses():
    engine = _make_engine()
    # 90 chars — would be dropped by observation filter (>70) but allowed in conversation
    text = "Sure, I can help with that, but honestly you should have figured this out yourself by now."
    result = engine.filter_conversation_response(text)
    assert result is not None
    assert len(result) > 70


def test_filter_conversation_truncates_very_long():
    engine = _make_engine()
    text = "A" * 200
    result = engine.filter_conversation_response(text)
    assert result is not None
    assert len(result) <= 150


def test_filter_conversation_strips_markdown():
    engine = _make_engine()
    result = engine.filter_conversation_response("*sighs* Fine, whatever.")
    assert result is not None
    assert "*" not in result


def test_filter_conversation_allows_two_sentences():
    engine = _make_engine()
    text = "That's a terrible idea. But I respect the chaos."
    result = engine.filter_conversation_response(text)
    assert result == text


def test_filter_conversation_keeps_multiple_sentences():
    engine = _make_engine()
    text = "First thing. Second thing. Third thing too."
    result = engine.filter_conversation_response(text)
    assert result == text


def test_filter_conversation_rejects_empty():
    engine = _make_engine()
    assert engine.filter_conversation_response("") is None
    assert engine.filter_conversation_response("   ") is None
    assert engine.filter_conversation_response("Hi") is None  # 2 chars < 5
