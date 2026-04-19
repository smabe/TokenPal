"""Tests for actions config loading."""

from __future__ import annotations

from tokenpal.config.loader import load_config
from tokenpal.config.schema import ActionsConfig, TokenPalConfig


def test_actions_config_defaults():
    cfg = ActionsConfig()
    assert cfg.enabled is True
    assert cfg.timer is True
    assert cfg.system_info is True
    assert cfg.open_app is True


def test_tokenpal_config_includes_actions():
    cfg = TokenPalConfig()
    assert isinstance(cfg.actions, ActionsConfig)


def test_load_config_parses_actions_section():
    cfg = load_config()
    assert cfg.actions.enabled is True
    assert cfg.actions.timer is True


def test_load_config_parses_cloud_llm_section(tmp_path, monkeypatch) -> None:
    """Regression: [cloud_llm] must be in _SECTION_MAP so enable flag
    survives a restart. Before the fix, the loader silently ignored the
    section and returned defaults (enabled=False)."""
    toml = tmp_path / "config.toml"
    toml.write_text(
        "[cloud_llm]\n"
        'enabled = true\n'
        'model = "claude-sonnet-4-6"\n'
        "timeout_s = 45.0\n"
    )
    cfg = load_config(config_path=toml)
    assert cfg.cloud_llm.enabled is True
    assert cfg.cloud_llm.model == "claude-sonnet-4-6"
    assert cfg.cloud_llm.timeout_s == 45.0
