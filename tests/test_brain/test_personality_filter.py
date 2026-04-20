"""Tests for PersonalityEngine.filter_response — cleanup + drop reasons."""

from __future__ import annotations

from tokenpal.brain.personality import FilterReason, PersonalityEngine
from tokenpal.tools.voice_profile import VoiceProfile


def _engine_with_anchors(*anchor_lines: str) -> PersonalityEngine:
    voice = VoiceProfile(
        character="testvoice",
        source="test",
        created="2026-04-20",
        lines=["plausible line " + str(i) for i in range(20)],
        anchor_lines=list(anchor_lines),
        persona="VOICE: generic.\n",
    )
    return PersonalityEngine(persona_prompt="", voice=voice)


def test_passes_plain_text_sets_ok_reason() -> None:
    eng = _engine_with_anchors()
    out = eng.filter_response("Hey there buddy, sounds like a solid plan today.")
    assert out is not None
    assert eng.last_filter_reason is FilterReason.OK


def test_anchor_regurgitation_suppressed() -> None:
    eng = _engine_with_anchors(
        "Why do I smell like pineapples?",
        "Is this a dream or a do-over?",
    )
    out = eng.filter_response("Why do I smell like pineapples?")
    assert out is None
    assert eng.last_filter_reason is FilterReason.ANCHOR_REGURGITATION


def test_anchor_regurgitation_ignores_punctuation() -> None:
    eng = _engine_with_anchors("Why do I smell like pineapples?")
    out = eng.filter_response("why do i smell like  pineapples")
    assert out is None
    assert eng.last_filter_reason is FilterReason.ANCHOR_REGURGITATION


def test_anchor_regurgitation_ignores_short_anchors() -> None:
    """Anchors < 15 chars are too generic to fingerprint — skipped."""
    eng = _engine_with_anchors("Yeah!", "Oh no!")
    out = eng.filter_response("Yeah! That's actually a great plan today, buddy.")
    assert out is not None
    assert eng.last_filter_reason is FilterReason.OK


def test_anchor_regurgitation_allows_paraphrase() -> None:
    eng = _engine_with_anchors("Why do I smell like pineapples?")
    out = eng.filter_response("Something smells fruity in here — maybe pineapples?")
    assert out is not None


def test_too_short_reason() -> None:
    eng = _engine_with_anchors()
    assert eng.filter_response("no.") is None
    assert eng.last_filter_reason is FilterReason.TOO_SHORT


def test_silent_marker_reason() -> None:
    eng = _engine_with_anchors()
    assert eng.filter_response("[SILENT] I have nothing to add here") is None
    assert eng.last_filter_reason is FilterReason.SILENT_MARKER


def test_reason_cleared_on_success() -> None:
    eng = _engine_with_anchors()
    eng.filter_response("no.")
    assert eng.last_filter_reason is FilterReason.TOO_SHORT
    eng.filter_response("Hey there buddy, that's actually a solid observation.")
    assert eng.last_filter_reason is FilterReason.OK


def test_enum_value_is_telemetry_friendly() -> None:
    """FilterReason inherits from str so .value works without `.value` boilerplate."""
    assert FilterReason.TOO_SHORT.value == "too_short"
    assert FilterReason.OK.value == ""
