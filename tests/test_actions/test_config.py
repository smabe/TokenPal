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
