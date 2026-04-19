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
        enabled=False, research_synth=True, research_plan=False,
        model="claude-haiku-4-5", key_fingerprint=None,
    )
    assert s.key_fingerprint is None


def test_cloud_modal_result_immutable() -> None:
    r = CloudModalResult(
        enabled=True, research_synth=True, research_plan=False,
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
        enabled=True, research_synth=True, research_plan=False,
        model="claude-haiku-4-5", new_api_key=key,
    )
    _apply_cloud_modal_result(result, cfg)
    assert cfg.cloud_llm.enabled is True
    assert cfg.cloud_llm.research_synth is True
    stored = json.loads(isolated["secrets_path"].read_text())
    assert stored["cloud_key"] == key


def test_apply_persists_key_at_0o600(
    isolated, cfg: TokenPalConfig
) -> None:
    key = "sk-ant-api03-" + "b" * 40
    result = CloudModalResult(
        enabled=True, research_synth=True, research_plan=False,
        model="claude-haiku-4-5", new_api_key=key,
    )
    _apply_cloud_modal_result(result, cfg)
    mode = stat.S_IMODE(isolated["secrets_path"].stat().st_mode)
    assert mode == 0o600


def test_apply_rejects_bad_key_without_touching_flags(
    isolated, cfg: TokenPalConfig
) -> None:
    result = CloudModalResult(
        enabled=True, research_synth=True, research_plan=True,
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
        enabled=True, research_synth=False, research_plan=True,
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
        enabled=True, research_synth=True, research_plan=False,
        model="claude-sonnet-4-6", new_api_key=None,
    )
    _apply_cloud_modal_result(result, cfg)
    assert cfg.cloud_llm.model == "claude-sonnet-4-6"
    assert isolated["toml_data"]["cloud_llm"]["model"] == "claude-sonnet-4-6"


def test_apply_ignores_unknown_model(isolated, cfg: TokenPalConfig) -> None:
    result = CloudModalResult(
        enabled=True, research_synth=True, research_plan=False,
        model="claude-opus-3", new_api_key=None,  # not allowlisted
    )
    _apply_cloud_modal_result(result, cfg)
    # Original model preserved
    assert cfg.cloud_llm.model == "claude-haiku-4-5"


def test_apply_no_key_no_write(isolated, cfg: TokenPalConfig) -> None:
    """User opens modal, changes toggles only, saves without touching key."""
    result = CloudModalResult(
        enabled=True, research_synth=False, research_plan=True,
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
            enabled=True, research_synth=True, research_plan=False,
            model="claude-haiku-4-5", new_api_key=key,
        ),
        cfg,
    )
    # Now disable via modal
    _apply_cloud_modal_result(
        CloudModalResult(
            enabled=False, research_synth=True, research_plan=False,
            model="claude-haiku-4-5", new_api_key=None,
        ),
        cfg,
    )
    assert cfg.cloud_llm.enabled is False
    # Key still on disk
    stored = json.loads(isolated["secrets_path"].read_text())
    assert stored["cloud_key"] == key
