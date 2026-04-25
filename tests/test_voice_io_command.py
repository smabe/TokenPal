"""Tests for the /voice-io slash command handler."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tokenpal.app import _handle_voice_io_command
from tokenpal.config.schema import AudioConfig, TokenPalConfig


@pytest.fixture()
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    config_path = tmp_path / "config.toml"
    state: dict[str, Any] = {"toml_data": {}, "config_path": config_path}

    def fake_update_config(mutate, **_kwargs):  # type: ignore[no-untyped-def]
        mutate(state["toml_data"])
        config_path.write_text(json.dumps(state["toml_data"]))
        return config_path

    monkeypatch.setattr(
        "tokenpal.config.audio_writer.update_config", fake_update_config
    )
    # Default: pretend audio deps ARE installed so existing tests that
    # don't care about the installer path don't trip the deps warning.
    monkeypatch.setattr(
        "tokenpal.audio.deps.missing_deps", lambda: (),
    )
    return state


@pytest.fixture()
def deps_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tokenpal.audio.deps.missing_deps",
        lambda: ("kokoro-onnx", "sounddevice"),
    )


@pytest.fixture()
def cfg() -> TokenPalConfig:
    return TokenPalConfig(audio=AudioConfig())


def test_bare_shows_state(isolated, cfg: TokenPalConfig) -> None:
    msg = _handle_voice_io_command("", cfg).message
    assert "voice off" in msg
    assert "ambient off" in msg


def test_on_flips_voice_and_persists(isolated, cfg: TokenPalConfig) -> None:
    result = _handle_voice_io_command("on", cfg)
    assert "voice on" in result.message
    assert cfg.audio.voice_conversation_enabled is True
    assert (
        isolated["toml_data"]["audio"]["voice_conversation_enabled"] is True
    )


def test_off_flips_voice_back(isolated, cfg: TokenPalConfig) -> None:
    cfg.audio.voice_conversation_enabled = True
    _handle_voice_io_command("off", cfg)
    assert cfg.audio.voice_conversation_enabled is False
    assert (
        isolated["toml_data"]["audio"]["voice_conversation_enabled"] is False
    )


def test_ambient_on_off(isolated, cfg: TokenPalConfig) -> None:
    _handle_voice_io_command("ambient on", cfg)
    assert cfg.audio.speak_ambient_enabled is True
    _handle_voice_io_command("ambient off", cfg)
    assert cfg.audio.speak_ambient_enabled is False


def test_voice_and_ambient_independent(isolated, cfg: TokenPalConfig) -> None:
    _handle_voice_io_command("on", cfg)
    _handle_voice_io_command("ambient on", cfg)
    assert cfg.audio.voice_conversation_enabled is True
    assert cfg.audio.speak_ambient_enabled is True
    _handle_voice_io_command("off", cfg)
    assert cfg.audio.voice_conversation_enabled is False
    assert cfg.audio.speak_ambient_enabled is True


def test_unknown_subcommand_returns_usage(
    isolated, cfg: TokenPalConfig,
) -> None:
    result = _handle_voice_io_command("garbage", cfg)
    assert "usage" in result.message.lower()


def test_ambient_without_value_returns_usage(
    isolated, cfg: TokenPalConfig,
) -> None:
    result = _handle_voice_io_command("ambient", cfg)
    assert "usage" in result.message.lower()
    assert cfg.audio.speak_ambient_enabled is False


def test_bare_warns_when_deps_missing(
    isolated, deps_missing, cfg: TokenPalConfig,
) -> None:
    msg = _handle_voice_io_command("", cfg).message
    assert "missing deps" in msg
    assert "kokoro-onnx" in msg
    assert "/voice-io install" in msg


def test_turning_on_warns_when_deps_missing(
    isolated, deps_missing, cfg: TokenPalConfig,
) -> None:
    msg = _handle_voice_io_command("on", cfg).message
    assert "voice on" in msg
    assert "missing deps" in msg


def test_turning_off_does_not_warn(
    isolated, deps_missing, cfg: TokenPalConfig,
) -> None:
    cfg.audio.voice_conversation_enabled = True
    msg = _handle_voice_io_command("off", cfg).message
    assert "voice off" in msg
    assert "missing deps" not in msg


def test_install_subcommand_invokes_installer(
    isolated, monkeypatch: pytest.MonkeyPatch, cfg: TokenPalConfig,
) -> None:
    from tokenpal.audio.deps import InstallResult

    called = {"n": 0}

    def fake_install(timeout_s: float = 600.0) -> InstallResult:
        called["n"] += 1
        return InstallResult(ok=True, message="installed: x. Restart to activate.")

    monkeypatch.setattr("tokenpal.audio.deps.install", fake_install)
    result = _handle_voice_io_command("install", cfg)
    assert called["n"] == 1
    assert "installed: x" in result.message


def test_install_failure_surfaces_error(
    isolated, monkeypatch: pytest.MonkeyPatch, cfg: TokenPalConfig,
) -> None:
    from tokenpal.audio.deps import InstallResult

    monkeypatch.setattr(
        "tokenpal.audio.deps.install",
        lambda timeout_s=600.0: InstallResult(
            ok=False, message="pip install failed (exit 1): blah",
        ),
    )
    result = _handle_voice_io_command("install", cfg)
    assert "pip install failed" in result.message
