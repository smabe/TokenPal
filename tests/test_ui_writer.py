"""Tests for config/ui_writer.py — persists [ui] chat_log_width."""

from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from tokenpal.config.ui_writer import set_chat_log_width


@pytest.fixture()
def fake_config(tmp_path: Path):
    path = tmp_path / "config.toml"
    with patch("tokenpal.config.toml_writer.find_config_toml", return_value=path):
        yield path


def _toml(path: Path) -> dict:
    return tomllib.loads(path.read_text())


def test_set_chat_log_width_creates_file(fake_config: Path) -> None:
    assert not fake_config.exists()
    set_chat_log_width(60)
    data = _toml(fake_config)
    assert data["ui"]["chat_log_width"] == 60


def test_set_chat_log_width_upserts_existing(fake_config: Path) -> None:
    fake_config.write_text(
        '[ui]\noverlay = "textual"\nchat_log_width = 40\n'
    )
    set_chat_log_width(72)
    data = _toml(fake_config)
    assert data["ui"]["chat_log_width"] == 72
    assert data["ui"]["overlay"] == "textual"


def test_set_chat_log_width_adds_section(fake_config: Path) -> None:
    fake_config.write_text("[memory]\nenabled = true\n")
    set_chat_log_width(50)
    data = _toml(fake_config)
    assert data["ui"]["chat_log_width"] == 50
    assert data["memory"]["enabled"] is True


def test_set_chat_log_width_coerces_to_int(fake_config: Path) -> None:
    set_chat_log_width(55.7)  # type: ignore[arg-type]
    data = _toml(fake_config)
    assert data["ui"]["chat_log_width"] == 55
