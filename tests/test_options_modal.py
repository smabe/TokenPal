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
from tokenpal.ui.options_modal import (
    OptionsModal,
    OptionsModalResult,
    OptionsModalState,
    ServerEntry,
    _canon_url,
    _same_server,
)


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


def test_result_carries_switch_server_to() -> None:
    r = OptionsModalResult(
        max_persisted=42,
        switch_server_to="http://localhost:11434/v1",
    )
    assert r.switch_server_to == "http://localhost:11434/v1"
    assert r.navigate_to is None


def test_result_defaults_switch_server_to_none() -> None:
    r = OptionsModalResult(max_persisted=42)
    assert r.switch_server_to is None


def test_state_accepts_known_servers_tuple() -> None:
    entries = (
        ServerEntry(
            url="http://localhost:11434/v1", label="local", model="gemma4"
        ),
        ServerEntry(
            url="http://10.0.0.2:8585/v1", label="remote", model=None
        ),
    )
    s = OptionsModalState(
        max_persisted=200,
        persist_enabled=True,
        current_api_url="http://localhost:11434/v1",
        known_servers=entries,
    )
    assert s.known_servers == entries
    assert s.known_servers[1].model is None


def test_state_known_servers_defaults_empty() -> None:
    s = OptionsModalState(max_persisted=200, persist_enabled=True)
    assert s.known_servers == ()
    assert s.current_api_url == ""


def test_result_carries_switch_model_to() -> None:
    r = OptionsModalResult(max_persisted=42, switch_model_to="gemma4")
    assert r.switch_model_to == "gemma4"
    assert r.switch_server_to is None


def test_result_defaults_switch_model_to_none() -> None:
    r = OptionsModalResult(max_persisted=42)
    assert r.switch_model_to is None


def test_state_accepts_available_models_and_current() -> None:
    s = OptionsModalState(
        max_persisted=200,
        persist_enabled=True,
        current_model="gemma4",
        available_models=("gemma4", "gemma2", "tokenpal-bmo"),
    )
    assert s.current_model == "gemma4"
    assert s.available_models == ("gemma4", "gemma2", "tokenpal-bmo")


def test_state_model_fields_default_empty() -> None:
    s = OptionsModalState(max_persisted=200, persist_enabled=True)
    assert s.current_model == ""
    assert s.available_models == ()


def test_result_carries_set_zip_and_wifi_label() -> None:
    r = OptionsModalResult(
        max_persisted=42, set_zip="90210", set_wifi_label="home"
    )
    assert r.set_zip == "90210"
    assert r.set_wifi_label == "home"


def test_result_defaults_zip_and_wifi_none() -> None:
    r = OptionsModalResult(max_persisted=42)
    assert r.set_zip is None
    assert r.set_wifi_label is None


def test_state_weather_and_wifi_label_defaults() -> None:
    s = OptionsModalState(max_persisted=200, persist_enabled=True)
    assert s.weather_label == ""
    assert s.current_wifi_label == ""


def test_same_server_canonicalizes() -> None:
    # Same URL with and without trailing /v1 or slash collapses.
    assert _same_server(
        "http://h:11434", "http://h:11434/v1"
    ) is True
    assert _same_server(
        "http://h:11434/v1/", "http://h:11434/v1"
    ) is True
    # Different hosts don't match.
    assert _same_server(
        "http://h:11434/v1", "http://other:11434/v1"
    ) is False
    # Empty strings never match (guards the "no current URL" case).
    assert _same_server("", "http://h:11434/v1") is False
    assert _same_server("http://h:11434/v1", "") is False


def _make_modal() -> OptionsModal:
    state = OptionsModalState(
        max_persisted=200,
        persist_enabled=True,
        current_api_url="http://localhost:11434/v1",
        current_model="gemma4",
        available_models=("gemma4", "gemma2"),
        known_servers=(
            ServerEntry(
                url="http://localhost:11434/v1", label="local", model="gemma4",
            ),
            ServerEntry(
                url="http://10.0.0.2:8585/v1", label="remote", model="qwen3",
            ),
        ),
    )
    return OptionsModal(state)


def test_canon_url_normalizes() -> None:
    assert _canon_url("http://h:11434") == "http://h:11434/v1"
    assert _canon_url("http://h:11434/") == "http://h:11434/v1"
    assert _canon_url("http://h:11434/v1/") == "http://h:11434/v1"
    assert _canon_url("") == ""


def test_modal_seeds_displayed_server_and_models() -> None:
    """At construction time the right column mirrors the active server's
    advertised models — no probe required."""
    m = _make_modal()
    assert m._displayed_server_url == "http://localhost:11434/v1"
    assert m._displayed_models() == ("gemma4", "gemma2")


def test_modal_active_model_for_current_vs_remembered() -> None:
    m = _make_modal()
    assert m._active_model_for("http://localhost:11434/v1") == "gemma4"
    # Non-current server: falls back to ServerEntry.model.
    assert m._active_model_for("http://10.0.0.2:8585/v1") == "qwen3"
    # Unknown server: empty.
    assert m._active_model_for("http://unknown:99/v1") == ""


def test_modal_collect_carries_both_pending_picks() -> None:
    m = _make_modal()
    m._pending_server = "http://10.0.0.2:8585/v1"
    m._pending_model = "qwen3"
    r = m._collect(clear_history=False)
    assert r.switch_server_to == "http://10.0.0.2:8585/v1"
    assert r.switch_model_to == "qwen3"


def test_modal_collect_omits_unset_picks() -> None:
    m = _make_modal()
    r = m._collect(clear_history=False)
    assert r.switch_server_to is None
    assert r.switch_model_to is None


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
