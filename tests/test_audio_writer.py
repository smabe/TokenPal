"""Tests for config/audio_writer.py — persists [audio] opt-in flags."""

from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from tokenpal.config.audio_writer import (
    set_speak_ambient_enabled,
    set_voice_conversation_enabled,
)


@pytest.fixture()
def fake_config(tmp_path: Path):
    path = tmp_path / "config.toml"
    with patch("tokenpal.config.toml_writer.find_config_toml", return_value=path):
        yield path


def _toml(path: Path) -> dict:
    return tomllib.loads(path.read_text())


def test_set_voice_conversation_creates_file(fake_config: Path) -> None:
    assert not fake_config.exists()
    set_voice_conversation_enabled(True)
    assert _toml(fake_config)["audio"]["voice_conversation_enabled"] is True


def test_set_speak_ambient_upserts_existing(fake_config: Path) -> None:
    fake_config.write_text(
        '[audio]\nvoice_conversation_enabled = true\nspeak_ambient_enabled = false\n'
    )
    set_speak_ambient_enabled(True)
    data = _toml(fake_config)["audio"]
    assert data["voice_conversation_enabled"] is True
    assert data["speak_ambient_enabled"] is True


def test_toggles_are_independent(fake_config: Path) -> None:
    set_voice_conversation_enabled(True)
    set_speak_ambient_enabled(True)
    set_voice_conversation_enabled(False)
    data = _toml(fake_config)["audio"]
    assert data["voice_conversation_enabled"] is False
    assert data["speak_ambient_enabled"] is True


def test_section_added_alongside_existing(fake_config: Path) -> None:
    fake_config.write_text('[memory]\nenabled = true\n')
    set_voice_conversation_enabled(True)
    data = _toml(fake_config)
    assert data["audio"]["voice_conversation_enabled"] is True
    assert data["memory"]["enabled"] is True
