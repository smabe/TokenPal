"""Tests for PersonalityEngine.filter_response — cleanup + drop reasons.

The buddy filter is the last line of defense between "LLM drifted into
gibberish or regurgitated a voice anchor" and the user. Every drop
reason is also surfaced via `_last_filter_reason` so telemetry can
attribute swallows without tailing logs.
"""

from __future__ import annotations

from tokenpal.brain.personality import PersonalityEngine
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


def test_passes_plain_text_sets_empty_reason() -> None:
    eng = _engine_with_anchors()
    out = eng.filter_response("Hey there buddy, sounds like a solid plan today.")
    assert out is not None
    assert eng._last_filter_reason == ""


def test_anchor_regurgitation_suppressed() -> None:
    """Verbatim copy of a voice anchor must be rejected."""
    eng = _engine_with_anchors(
        "Why do I smell like pineapples?",
        "Is this a dream or a do-over?",
    )
    out = eng.filter_response("Why do I smell like pineapples?")
    assert out is None
    assert eng._last_filter_reason == "anchor_regurgitation"


def test_anchor_regurgitation_ignores_punctuation() -> None:
    """Case + punctuation + extra spaces must not defeat the match."""
    eng = _engine_with_anchors("Why do I smell like pineapples?")
    out = eng.filter_response("why do i smell like  pineapples")
    assert out is None
    assert eng._last_filter_reason == "anchor_regurgitation"


def test_anchor_regurgitation_ignores_short_anchors() -> None:
    """Anchors < 15 chars would fail the length gate anyway and are too
    generic to reliably fingerprint a regurgitation (e.g. 'Yeah!').
    """
    eng = _engine_with_anchors("Yeah!", "Oh no!")
    # Plain text that happens to contain a short anchor substring should
    # still pass — it's a normal generation, not a regurgitation.
    out = eng.filter_response("Yeah! That's actually a great plan today, buddy.")
    assert out is not None
    assert eng._last_filter_reason == ""


def test_anchor_regurgitation_allows_paraphrase() -> None:
    """The LLM paraphrasing an anchor line is fine; we only guard verbatim."""
    eng = _engine_with_anchors("Why do I smell like pineapples?")
    out = eng.filter_response("Something smells fruity in here — maybe pineapples?")
    assert out is not None


def test_too_short_reason() -> None:
    eng = _engine_with_anchors()
    assert eng.filter_response("no.") is None
    assert eng._last_filter_reason == "too_short"


def test_silent_marker_reason() -> None:
    eng = _engine_with_anchors()
    assert eng.filter_response("[SILENT] I have nothing to add here") is None
    assert eng._last_filter_reason == "silent_marker"


def test_reason_cleared_on_success() -> None:
    """Reason set by a prior failed call must not leak into the next success."""
    eng = _engine_with_anchors()
    eng.filter_response("no.")
    assert eng._last_filter_reason == "too_short"
    eng.filter_response("Hey there buddy, that's actually a solid observation.")
    assert eng._last_filter_reason == ""
