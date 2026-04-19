"""Tests for the OptionsModal result machinery + chatlog_writer helpers.

The Textual modal rendering + keyboard interaction tests need a full
harness and aren't worth the weight. We exercise OptionsModalResult,
clamp_max_persisted, and set_max_persisted against the pure helpers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tokenpal.config.chatlog_writer import (
    MAX_PERSISTED,
    MIN_PERSISTED,
    clamp_max_persisted,
    set_max_persisted,
)
from tokenpal.ui.options_modal import OptionsModalResult, OptionsModalState


def test_state_and_result_dataclasses_are_frozen() -> None:
    s = OptionsModalState(max_persisted=200, persist_enabled=True)
    r = OptionsModalResult(max_persisted=100)
    with pytest.raises(AttributeError):
        s.max_persisted = 500  # type: ignore[misc]
    with pytest.raises(AttributeError):
        r.max_persisted = 500  # type: ignore[misc]


def test_result_defaults_navigate_none_no_clear() -> None:
    r = OptionsModalResult(max_persisted=42)
    assert r.navigate_to is None
    assert r.clear_history is False


def test_result_accepts_voice_navigate_target() -> None:
    r = OptionsModalResult(max_persisted=42, navigate_to="voice")
    assert r.navigate_to == "voice"


def test_clamp_enforces_min_max() -> None:
    assert clamp_max_persisted(-1) == MIN_PERSISTED
    assert clamp_max_persisted(0) == MIN_PERSISTED
    assert clamp_max_persisted(200) == 200
    assert clamp_max_persisted(99_999) == MAX_PERSISTED
    assert clamp_max_persisted(MAX_PERSISTED + 1) == MAX_PERSISTED


def test_clamp_handles_non_digit_safely() -> None:
    """clamp takes an int. If something upstream failed to parse, the
    modal's _read_max_persisted falls back to the stored value — never
    passes a string in here. Belt-and-suspenders: int() coercion raises
    on unparseable input, which the modal catches."""
    with pytest.raises((ValueError, TypeError)):
        clamp_max_persisted("1; DROP TABLE chat_log")  # type: ignore[arg-type]


def test_set_max_persisted_writes_clamped_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    def fake_update_config(mutate, **_kwargs):  # type: ignore[no-untyped-def]
        data: dict[str, Any] = {}
        mutate(data)
        captured["data"] = data
        return tmp_path / "config.toml"

    monkeypatch.setattr(
        "tokenpal.config.chatlog_writer.update_config", fake_update_config
    )

    set_max_persisted(99_999)
    assert captured["data"] == {"chat_log": {"max_persisted": MAX_PERSISTED}}

    set_max_persisted(150)
    assert captured["data"] == {"chat_log": {"max_persisted": 150}}

    set_max_persisted(-5)
    assert captured["data"] == {"chat_log": {"max_persisted": MIN_PERSISTED}}
