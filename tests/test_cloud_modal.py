"""Tests for the CloudModal UI state machine and the apply-result helper.

The Textual modal itself is covered at the dataclass / pure-function level
only - rendering + keyboard interaction tests require a full Textual
test harness and aren't worth the weight for a simple settings screen.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

import pytest

from tokenpal.app import _apply_cloud_modal_result
from tokenpal.config.schema import CloudLLMConfig, TokenPalConfig
from tokenpal.ui.cloud_modal import CloudModalResult, CloudModalState


@pytest.fixture()
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    secrets_path = tmp_path / ".secrets.json"
    state: dict[str, Any] = {"secrets_path": secrets_path, "toml_data": {}}

    monkeypatch.setattr(
        "tokenpal.config.secrets._default_path", lambda: secrets_path
    )

    def fake_update_config(mutate, **_kwargs):  # type: ignore[no-untyped-def]
        mutate(state["toml_data"])
        return tmp_path / "config.toml"

    monkeypatch.setattr(
        "tokenpal.config.cloud_writer.update_config", fake_update_config
    )
    monkeypatch.setattr(
        "tokenpal.config.toml_writer.update_config", fake_update_config
    )
    return state


@pytest.fixture()
def cfg() -> TokenPalConfig:
    return TokenPalConfig(cloud_llm=CloudLLMConfig())


# ---------------------------------------------------------------------------
# State dataclasses
# ---------------------------------------------------------------------------


def test_cloud_modal_state_with_no_key() -> None:
    s = CloudModalState(
        enabled=False, research_synth=True, research_plan=False, research_deep=False, research_search=False,
        model="claude-haiku-4-5", key_fingerprint=None,
    )
    assert s.key_fingerprint is None


def test_cloud_modal_result_immutable() -> None:
    r = CloudModalResult(
        enabled=True, research_synth=True, research_plan=False, research_deep=False, research_search=False,
        model="claude-haiku-4-5", new_api_key=None,
    )
    with pytest.raises((AttributeError, Exception)):
        r.enabled = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Apply result helper
# ---------------------------------------------------------------------------


def test_apply_saves_new_key_and_flips_enabled(
    isolated, cfg: TokenPalConfig
) -> None:
    key = "sk-ant-api03-" + "a" * 40
    result = CloudModalResult(
        enabled=True, research_synth=True, research_plan=False, research_deep=False, research_search=False,
        model="claude-haiku-4-5", new_api_key=key,
    )
    _apply_cloud_modal_result(result, cfg)
    assert cfg.cloud_llm.enabled is True
    assert cfg.cloud_llm.research_synth is True
    stored = json.loads(isolated["secrets_path"].read_text())
    assert stored["anthropic_key"] == key


def test_apply_persists_key_at_0o600(
    isolated, cfg: TokenPalConfig
) -> None:
    key = "sk-ant-api03-" + "b" * 40
    result = CloudModalResult(
        enabled=True, research_synth=True, research_plan=False, research_deep=False, research_search=False,
        model="claude-haiku-4-5", new_api_key=key,
    )
    _apply_cloud_modal_result(result, cfg)
    mode = stat.S_IMODE(isolated["secrets_path"].stat().st_mode)
    assert mode == 0o600


def test_apply_rejects_bad_key_without_touching_flags(
    isolated, cfg: TokenPalConfig
) -> None:
    result = CloudModalResult(
        enabled=True, research_synth=True, research_plan=True, research_deep=False, research_search=False,
        model="claude-haiku-4-5", new_api_key="not-a-real-key",
    )
    _apply_cloud_modal_result(result, cfg)
    # enabled stays False because the bad-key branch bails before flipping
    assert cfg.cloud_llm.enabled is False
    # No key written
    assert not isolated["secrets_path"].exists() or \
        "cloud_key" not in json.loads(isolated["secrets_path"].read_text())


def test_apply_flips_site_flags(isolated, cfg: TokenPalConfig) -> None:
    result = CloudModalResult(
        enabled=True, research_synth=False, research_plan=True, research_deep=False, research_search=False,
        model="claude-haiku-4-5", new_api_key=None,
    )
    _apply_cloud_modal_result(result, cfg)
    assert cfg.cloud_llm.research_synth is False
    assert cfg.cloud_llm.research_plan is True
    section = isolated["toml_data"]["cloud_llm"]
    assert section["research_synth"] is False
    assert section["research_plan"] is True


def test_apply_changes_model(isolated, cfg: TokenPalConfig) -> None:
    result = CloudModalResult(
        enabled=True, research_synth=True, research_plan=False, research_deep=False, research_search=False,
        model="claude-sonnet-4-6", new_api_key=None,
    )
    _apply_cloud_modal_result(result, cfg)
    assert cfg.cloud_llm.model == "claude-sonnet-4-6"
    assert isolated["toml_data"]["cloud_llm"]["model"] == "claude-sonnet-4-6"


def test_apply_ignores_unknown_model(isolated, cfg: TokenPalConfig) -> None:
    result = CloudModalResult(
        enabled=True, research_synth=True, research_plan=False, research_deep=False, research_search=False,
        model="claude-opus-3", new_api_key=None,  # not allowlisted
    )
    _apply_cloud_modal_result(result, cfg)
    # Original model preserved
    assert cfg.cloud_llm.model == "claude-haiku-4-5"


def test_apply_no_key_no_write(isolated, cfg: TokenPalConfig) -> None:
    """User opens modal, changes toggles only, saves without touching key."""
    result = CloudModalResult(
        enabled=True, research_synth=False, research_plan=True, research_deep=False, research_search=False,
        model="claude-haiku-4-5", new_api_key=None,
    )
    _apply_cloud_modal_result(result, cfg)
    # Secrets file untouched
    assert not isolated["secrets_path"].exists()
    # Flags still applied
    assert cfg.cloud_llm.research_synth is False
    assert cfg.cloud_llm.research_plan is True


def test_apply_disable_preserves_key(isolated, cfg: TokenPalConfig) -> None:
    # Store a key first
    key = "sk-ant-api03-" + "c" * 40
    _apply_cloud_modal_result(
        CloudModalResult(
            enabled=True, research_synth=True, research_plan=False, research_deep=False, research_search=False,
            model="claude-haiku-4-5", new_api_key=key,
        ),
        cfg,
    )
    # Now disable via modal
    _apply_cloud_modal_result(
        CloudModalResult(
            enabled=False, research_synth=True, research_plan=False, research_deep=False, research_search=False,
            model="claude-haiku-4-5", new_api_key=None,
        ),
        cfg,
    )
    assert cfg.cloud_llm.enabled is False
    # Key still on disk
    stored = json.loads(isolated["secrets_path"].read_text())
    assert stored["anthropic_key"] == key


# ---------------------------------------------------------------------------
# Deep-mode flag
# ---------------------------------------------------------------------------


def test_apply_persists_deep_flag_for_sonnet(
    isolated, cfg: TokenPalConfig
) -> None:
    result = CloudModalResult(
        enabled=True, research_synth=True, research_plan=False,
        research_deep=True, research_search=False, model="claude-sonnet-4-6", new_api_key=None,
    )
    _apply_cloud_modal_result(result, cfg)
    assert cfg.cloud_llm.research_deep is True
    assert isolated["toml_data"]["cloud_llm"]["research_deep"] is True


def test_apply_clears_deep_flag(isolated, cfg: TokenPalConfig) -> None:
    cfg.cloud_llm.research_deep = True
    result = CloudModalResult(
        enabled=True, research_synth=True, research_plan=False,
        research_deep=False, research_search=False, model="claude-sonnet-4-6", new_api_key=None,
    )
    _apply_cloud_modal_result(result, cfg)
    assert cfg.cloud_llm.research_deep is False


def test_apply_persists_search_flag_for_sonnet(
    isolated, cfg: TokenPalConfig
) -> None:
    result = CloudModalResult(
        enabled=True, research_synth=True, research_plan=False,
        research_deep=False, research_search=True,
        model="claude-sonnet-4-6", new_api_key=None,
    )
    _apply_cloud_modal_result(result, cfg)
    assert cfg.cloud_llm.research_search is True
    assert isolated["toml_data"]["cloud_llm"]["research_search"] is True


def test_collect_forces_deep_off_for_haiku() -> None:
    """If the user set deep via headless-path state but the radio ends on
    Haiku, _collect must force deep off — the runtime gate would reject
    any deep attempt anyway."""
    from tokenpal.ui.cloud_modal import CloudModal

    state = CloudModalState(
        enabled=True, research_synth=True, research_plan=False,
        research_deep=True, research_search=False, model="claude-sonnet-4-6", key_fingerprint="ab*",
    )
    modal = CloudModal(state)

    class _Stub:
        def __init__(self, value: bool | str, disabled: bool = False) -> None:
            self.value = value
            self.disabled = disabled
            self.label = value

    class _RadioStub:
        def __init__(self, label: str) -> None:
            self.pressed_button = _Stub(label)

    widgets = {
        "#toggle-enabled": _Stub(True),
        "#toggle-synth": _Stub(True),
        "#toggle-plan": _Stub(False),
        "#toggle-deep": _Stub(True),
        "#model-set": _RadioStub("claude-haiku-4-5"),
        "#api-key-input": _Stub("", disabled=True),
    }

    def _query_one(sel: str, _cls: Any = None) -> Any:
        return widgets[sel]

    modal.query_one = _query_one  # type: ignore[assignment]
    result = modal._collect()
    assert result.model == "claude-haiku-4-5"
    assert result.research_deep is False


# ---------------------------------------------------------------------------
# Tavily + Brave sections
# ---------------------------------------------------------------------------


class _Stub:
    def __init__(self, value: bool | str, disabled: bool = False) -> None:
        self.value = value
        self.disabled = disabled
        self.label = value


class _RadioStub:
    def __init__(self, label: str) -> None:
        self.pressed_button = _Stub(label)


def _baseline_widgets(
    *,
    model: str = "claude-haiku-4-5",
    api_key_value: str = "",
    api_key_disabled: bool = True,
    tavily_enabled: bool = False,
    tavily_depth: str = "advanced",
    tavily_key_value: str = "",
    tavily_key_disabled: bool = True,
    brave_key_value: str = "",
    brave_key_disabled: bool = True,
) -> dict[str, Any]:
    return {
        "#toggle-enabled": _Stub(True),
        "#toggle-synth": _Stub(True),
        "#toggle-plan": _Stub(False),
        "#toggle-deep": _Stub(False),
        "#toggle-search": _Stub(False),
        "#model-set": _RadioStub(model),
        "#api-key-input": _Stub(api_key_value, disabled=api_key_disabled),
        "#toggle-tavily-enabled": _Stub(tavily_enabled),
        "#tavily-depth-set": _RadioStub(tavily_depth),
        "#tavily-api-key-input": _Stub(
            tavily_key_value, disabled=tavily_key_disabled,
        ),
        "#brave-api-key-input": _Stub(
            brave_key_value, disabled=brave_key_disabled,
        ),
    }


def _install_query(modal: Any, widgets: dict[str, Any]) -> None:
    def _query_one(sel: str, _cls: Any = None) -> Any:
        return widgets[sel]

    modal.query_one = _query_one


def test_modal_carries_tavily_state_to_result() -> None:
    """State → modal → result: Tavily key + depth + enabled round-trip."""
    from tokenpal.ui.cloud_modal import CloudModal

    state = CloudModalState(
        enabled=True, research_synth=True, research_plan=False,
        research_deep=False, research_search=False,
        model="claude-haiku-4-5",
        key_fingerprint="sk-ant-...aaaa",
        tavily_enabled=True,
        tavily_search_depth="advanced",
        tavily_key_fingerprint="tvly-...bbbb",
        brave_key_fingerprint=None,
    )
    modal = CloudModal(state)
    widgets = _baseline_widgets(
        tavily_enabled=True,
        tavily_depth="advanced",
        # User did not toggle "Replace stored key" → input stays disabled
        tavily_key_disabled=True,
    )
    _install_query(modal, widgets)

    result = modal._collect()
    assert result.tavily_enabled is True
    assert result.tavily_search_depth == "advanced"
    # No new key typed → None (caller keeps the on-disk value)
    assert result.tavily_new_api_key is None


def test_modal_accepts_new_tavily_key() -> None:
    """No Tavily key on disk → user types one → result carries it."""
    from tokenpal.ui.cloud_modal import CloudModal

    state = CloudModalState(
        enabled=False, research_synth=True, research_plan=False,
        research_deep=False, research_search=False,
        model="claude-haiku-4-5",
        key_fingerprint=None,
        tavily_enabled=False,
        tavily_search_depth="advanced",
        tavily_key_fingerprint=None,
        brave_key_fingerprint=None,
    )
    modal = CloudModal(state)
    widgets = _baseline_widgets(
        tavily_enabled=True,
        tavily_key_value="tvly-xyz",
        tavily_key_disabled=False,
    )
    _install_query(modal, widgets)

    result = modal._collect()
    assert result.tavily_new_api_key == "tvly-xyz"
    assert result.tavily_enabled is True


def test_modal_carries_brave_state_to_result() -> None:
    """Brave has no flags — only the key round-trips."""
    from tokenpal.ui.cloud_modal import CloudModal

    state = CloudModalState(
        enabled=False, research_synth=True, research_plan=False,
        research_deep=False, research_search=False,
        model="claude-haiku-4-5",
        key_fingerprint=None,
        tavily_enabled=False,
        tavily_search_depth="advanced",
        tavily_key_fingerprint=None,
        brave_key_fingerprint=None,
    )
    modal = CloudModal(state)
    widgets = _baseline_widgets(
        brave_key_value="BSA-abcdefghijklmnop1234",
        brave_key_disabled=False,
    )
    _install_query(modal, widgets)

    result = modal._collect()
    assert result.brave_new_api_key == "BSA-abcdefghijklmnop1234"
    # Typing a brave key must not leak into tavily / anthropic result fields
    assert result.tavily_new_api_key is None
    assert result.new_api_key is None


def test_modal_replace_tavily_key_toggle() -> None:
    """The tavily replace-key checkbox toggles the input's disabled state."""
    from tokenpal.ui.cloud_modal import CloudModal

    state = CloudModalState(
        enabled=True, research_synth=True, research_plan=False,
        research_deep=False, research_search=False,
        model="claude-haiku-4-5",
        key_fingerprint="sk-ant-...aaaa",
        tavily_enabled=True,
        tavily_search_depth="advanced",
        tavily_key_fingerprint="tvly-...bbbb",
        brave_key_fingerprint=None,
    )
    modal = CloudModal(state)

    class _InputStub(_Stub):
        def focus(self) -> None:
            pass

    tavily_input_stub = _InputStub("", disabled=True)

    def _query_one(sel: str, _cls: Any = None) -> Any:
        if sel == "#tavily-api-key-input":
            return tavily_input_stub
        raise KeyError(sel)

    modal.query_one = _query_one  # type: ignore[assignment]

    class _Ev:
        def __init__(self, cb_id: str, value: bool) -> None:
            self.checkbox = _CheckboxStub(cb_id)
            self.value = value

    class _CheckboxStub:
        def __init__(self, cb_id: str) -> None:
            self.id = cb_id

    # Simulate the Textual event. We reuse the modal's handler directly,
    # bypassing Textual's own Checkbox.Changed type.
    modal.on_checkbox_changed(_Ev("tavily-replace-key", True))  # type: ignore[arg-type]
    assert tavily_input_stub.disabled is False

    modal.on_checkbox_changed(_Ev("tavily-replace-key", False))  # type: ignore[arg-type]
    assert tavily_input_stub.disabled is True


def test_modal_all_three_backends_coexist(
    isolated, cfg: TokenPalConfig,
) -> None:
    """All three keys set + Tavily flags saved together persist correctly."""
    anthropic_key = "sk-ant-api03-" + "d" * 40
    tavily_key = "tvly-" + "e" * 32
    brave_key = "BSA" + "f" * 32

    result = CloudModalResult(
        enabled=True, research_synth=True, research_plan=False,
        research_deep=False, research_search=False,
        model="claude-haiku-4-5",
        new_api_key=anthropic_key,
        tavily_enabled=True,
        tavily_search_depth="basic",
        tavily_new_api_key=tavily_key,
        brave_new_api_key=brave_key,
    )
    _apply_cloud_modal_result(result, cfg)

    stored = json.loads(isolated["secrets_path"].read_text())
    assert stored["anthropic_key"] == anthropic_key
    assert stored["tavily_key"] == tavily_key
    assert stored["brave_key"] == brave_key

    # cloud_search section persisted
    cs_section = isolated["toml_data"].get("cloud_search", {})
    assert cs_section.get("enabled") is True
    assert cs_section.get("search_depth") == "basic"

    # In-memory config mirrors disk
    assert cfg.cloud_search.enabled is True
    assert cfg.cloud_search.search_depth == "basic"
    assert cfg.cloud_llm.enabled is True
