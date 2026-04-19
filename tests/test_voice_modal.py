"""Tests for VoiceModal dataclasses + result shape.

Textual rendering tests need a full harness and aren't worth the weight
(same convention as test_options_modal.py / test_cloud_modal.py). We
exercise the pure state/result dataclasses and the helper formatters.
"""

from __future__ import annotations

import pytest

from tokenpal.tools.voice_profile import ProfileSummary
from tokenpal.ui.voice_modal import (
    VoiceModalResult,
    VoiceModalState,
    _format_saved_row,
    _format_status,
)


def _summary(
    *, slug: str = "finn", character: str = "Finn", lines: int = 5,
    source: str = "adventuretime.fandom.com",
    finetuned_model: str = "",
) -> ProfileSummary:
    return ProfileSummary(
        slug=slug,
        character=character,
        line_count=lines,
        source=source,
        finetuned_model=finetuned_model,
    )


def test_state_is_frozen() -> None:
    s = VoiceModalState(active_voice=None)
    with pytest.raises(AttributeError):
        s.active_voice = _summary()  # type: ignore[misc]


def test_result_is_frozen_with_default_payload() -> None:
    r = VoiceModalResult(action="off")
    assert r.payload == {}
    with pytest.raises(AttributeError):
        r.action = "switch"  # type: ignore[misc]


def test_result_round_trip_switch() -> None:
    r = VoiceModalResult(action="switch", payload={"name": "finn"})
    assert r.action == "switch"
    assert r.payload == {"name": "finn"}


def test_state_default_saved_is_empty_list() -> None:
    s = VoiceModalState(active_voice=None)
    assert s.saved == []


def test_format_status_default_voice() -> None:
    s = VoiceModalState(active_voice=None)
    assert _format_status(s) == "Default TokenPal voice."


def test_format_status_custom_voice() -> None:
    s = VoiceModalState(
        active_voice=_summary(character="Finn", lines=42),
    )
    out = _format_status(s)
    assert "Finn" in out
    assert "42 lines" in out
    assert "adventuretime.fandom.com" in out
    assert "fine-tuned" not in out


def test_format_status_finetuned_voice() -> None:
    s = VoiceModalState(
        active_voice=_summary(
            character="Jake", lines=10, finetuned_model="tokenpal-jake",
        ),
    )
    out = _format_status(s)
    assert "Jake (fine-tuned)" in out
    assert "tokenpal-jake" in out


def test_format_saved_row_plain() -> None:
    assert _format_saved_row(_summary(character="Finn", lines=3)) == (
        "Finn (3 lines)"
    )


def test_format_saved_row_finetuned_has_ft_marker() -> None:
    row = _format_saved_row(
        _summary(character="Jake", lines=9, finetuned_model="tokenpal-jake"),
    )
    assert row == "Jake (9 lines) [FT]"
