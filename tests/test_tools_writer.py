"""Tests for tokenpal/config/tools_writer.py — /tools picker persistence."""

from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from tokenpal.config.tools_writer import set_enabled_tools, set_tool_enabled


@pytest.fixture()
def fake_config(tmp_path: Path):
    path = tmp_path / "config.toml"
    with patch("tokenpal.config.toml_writer.find_config_toml", return_value=path):
        yield path


def _toml(path: Path) -> dict:
    return tomllib.loads(path.read_text())


def test_set_enabled_tools_creates_file(fake_config: Path) -> None:
    assert not fake_config.exists()
    set_enabled_tools(["read_file", "git_log"])
    data = _toml(fake_config)
    assert data["tools"]["enabled_tools"] == ["git_log", "read_file"]


def test_set_enabled_tools_overwrites(fake_config: Path) -> None:
    fake_config.write_text('[tools]\nenabled_tools = ["old_one"]\n')
    set_enabled_tools(["fresh"])
    data = _toml(fake_config)
    assert data["tools"]["enabled_tools"] == ["fresh"]


def test_set_enabled_tools_dedupes_and_sorts(fake_config: Path) -> None:
    set_enabled_tools(["b", "a", "b", "c", "a"])
    data = _toml(fake_config)
    assert data["tools"]["enabled_tools"] == ["a", "b", "c"]


def test_set_enabled_tools_preserves_other_sections(fake_config: Path) -> None:
    fake_config.write_text("[memory]\nenabled = true\n")
    set_enabled_tools(["x"])
    data = _toml(fake_config)
    assert data["memory"]["enabled"] is True
    assert data["tools"]["enabled_tools"] == ["x"]


def test_set_tool_enabled_adds(fake_config: Path) -> None:
    fake_config.write_text('[tools]\nenabled_tools = ["a"]\n')
    set_tool_enabled("b", True)
    data = _toml(fake_config)
    assert data["tools"]["enabled_tools"] == ["a", "b"]


def test_set_tool_enabled_removes(fake_config: Path) -> None:
    fake_config.write_text('[tools]\nenabled_tools = ["a", "b"]\n')
    set_tool_enabled("a", False)
    data = _toml(fake_config)
    assert data["tools"]["enabled_tools"] == ["b"]


def test_set_tool_enabled_is_idempotent(fake_config: Path) -> None:
    set_tool_enabled("a", True)
    set_tool_enabled("a", True)
    data = _toml(fake_config)
    assert data["tools"]["enabled_tools"] == ["a"]


def test_set_tool_enabled_rejects_empty(fake_config: Path) -> None:
    with pytest.raises(ValueError):
        set_tool_enabled("", True)
