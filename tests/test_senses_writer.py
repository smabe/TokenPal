"""Tests for config/senses_writer.py — the /senses and /wifi config mutators."""

from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from tokenpal.config.senses_writer import set_sense_enabled, set_ssid_label


@pytest.fixture()
def fake_config(tmp_path: Path):
    path = tmp_path / "config.toml"
    with patch("tokenpal.config.senses_writer._config_path", return_value=path):
        yield path


def _toml(path: Path) -> dict:
    return tomllib.loads(path.read_text())


def test_set_sense_enabled_creates_file(fake_config: Path) -> None:
    assert not fake_config.exists()
    set_sense_enabled("battery", True)
    data = _toml(fake_config)
    assert data["senses"]["battery"] is True


def test_set_sense_enabled_flips_existing(fake_config: Path) -> None:
    fake_config.write_text("[senses]\nbattery = false\nhardware = true\n")
    set_sense_enabled("battery", True)
    data = _toml(fake_config)
    assert data["senses"]["battery"] is True
    assert data["senses"]["hardware"] is True


def test_set_sense_enabled_adds_under_existing_section(fake_config: Path) -> None:
    fake_config.write_text("[senses]\nhardware = true\n\n[memory]\nenabled = true\n")
    set_sense_enabled("process_heat", True)
    data = _toml(fake_config)
    assert data["senses"]["process_heat"] is True
    assert data["senses"]["hardware"] is True
    assert data["memory"]["enabled"] is True


def test_set_sense_enabled_appends_section(fake_config: Path) -> None:
    fake_config.write_text("[memory]\nenabled = true\n")
    set_sense_enabled("battery", True)
    data = _toml(fake_config)
    assert data["senses"]["battery"] is True
    assert data["memory"]["enabled"] is True


def test_set_sense_disable(fake_config: Path) -> None:
    fake_config.write_text("[senses]\nbattery = true\n")
    set_sense_enabled("battery", False)
    data = _toml(fake_config)
    assert data["senses"]["battery"] is False


def test_set_ssid_label_rejects_non_hash(fake_config: Path) -> None:
    with pytest.raises(ValueError):
        set_ssid_label("nothex", "home")
    with pytest.raises(ValueError):
        set_ssid_label("abcd1234", "home")  # wrong length


def test_set_ssid_label_creates_section(fake_config: Path) -> None:
    set_ssid_label("a" * 16, "home")
    data = _toml(fake_config)
    assert data["network_state"]["ssid_labels"] == {"aaaaaaaaaaaaaaaa": "home"}


def test_set_ssid_label_upserts_existing(fake_config: Path) -> None:
    fake_config.write_text(
        '[network_state]\n'
        'ssid_labels = { "aaaaaaaaaaaaaaaa" = "home", '
        '"bbbbbbbbbbbbbbbb" = "coffee" }\n'
    )
    set_ssid_label("a" * 16, "home office")
    set_ssid_label("c" * 16, "gym")
    data = _toml(fake_config)
    labels = data["network_state"]["ssid_labels"]
    assert labels["aaaaaaaaaaaaaaaa"] == "home office"
    assert labels["bbbbbbbbbbbbbbbb"] == "coffee"
    assert labels["cccccccccccccccc"] == "gym"


def test_set_ssid_label_escapes_quotes(fake_config: Path) -> None:
    set_ssid_label("a" * 16, 'Bob\'s "wifi"')
    data = _toml(fake_config)
    assert data["network_state"]["ssid_labels"]["a" * 16] == 'Bob\'s "wifi"'


def test_set_ssid_label_quoted_label_survives_upsert(fake_config: Path) -> None:
    """Quoted labels must still round-trip after a second upsert re-parses the file."""
    set_ssid_label("a" * 16, 'Bob\'s "wifi"')
    set_ssid_label("b" * 16, "coffee")
    data = _toml(fake_config)
    assert data["network_state"]["ssid_labels"]["a" * 16] == 'Bob\'s "wifi"'
    assert data["network_state"]["ssid_labels"]["b" * 16] == "coffee"


def test_set_ssid_label_appends_when_section_missing(fake_config: Path) -> None:
    fake_config.write_text("[memory]\nenabled = true\n")
    set_ssid_label("a" * 16, "home")
    data = _toml(fake_config)
    assert data["memory"]["enabled"] is True
    assert data["network_state"]["ssid_labels"]["a" * 16] == "home"
