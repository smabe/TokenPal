"""Tests for the /idle_tools config writer."""

from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from tokenpal.config.idle_tools_writer import (
    set_idle_rule_enabled,
    set_idle_tools_enabled,
)


@pytest.fixture()
def fake_config(tmp_path: Path):
    path = tmp_path / "config.toml"
    with patch("tokenpal.config.toml_writer.find_config_toml", return_value=path):
        yield path


def _toml(path: Path) -> dict:
    return tomllib.loads(path.read_text())


def test_set_global_enabled_writes_flag(fake_config: Path) -> None:
    set_idle_tools_enabled(True)
    assert _toml(fake_config)["idle_tools"]["enabled"] is True


def test_set_global_disabled_flips_flag(fake_config: Path) -> None:
    set_idle_tools_enabled(True)
    set_idle_tools_enabled(False)
    assert _toml(fake_config)["idle_tools"]["enabled"] is False


def test_set_rule_enabled_upserts_rule(fake_config: Path) -> None:
    set_idle_rule_enabled("morning_word", True)
    data = _toml(fake_config)
    assert data["idle_tools"]["rules"]["morning_word"] is True


def test_set_rule_enabled_round_trips(fake_config: Path) -> None:
    set_idle_rule_enabled("evening_moon", True)
    set_idle_rule_enabled("evening_moon", False)
    data = _toml(fake_config)
    assert data["idle_tools"]["rules"]["evening_moon"] is False


def test_global_and_rule_coexist(fake_config: Path) -> None:
    set_idle_tools_enabled(True)
    set_idle_rule_enabled("monday_joke", False)
    data = _toml(fake_config)
    assert data["idle_tools"]["enabled"] is True
    assert data["idle_tools"]["rules"]["monday_joke"] is False
