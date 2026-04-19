"""Tests for the /cloud slash command handler.

Uses monkeypatch to redirect the secrets path and TOML writer to tmpdir,
so tests can exercise the full store/flip/forget/model flow without
touching the user's real ~/.tokenpal.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

import pytest

from tokenpal.app import _handle_cloud_command
from tokenpal.config.schema import CloudLLMConfig, TokenPalConfig


@pytest.fixture()
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """Redirect the secrets file + TOML writer to tmpdir. Returns handles."""
    secrets_path = tmp_path / ".secrets.json"
    config_path = tmp_path / "config.toml"
    state: dict[str, Any] = {
        "secrets_path": secrets_path,
        "toml_data": {},
        "config_path": config_path,
    }

    # Redirect secrets storage
    monkeypatch.setattr(
        "tokenpal.config.secrets._default_path", lambda: secrets_path
    )

    # Stub out the TOML writer — /cloud enable/disable/model call it.
    def fake_update_config(mutate, **_kwargs):  # type: ignore[no-untyped-def]
        mutate(state["toml_data"])
        config_path.write_text(json.dumps(state["toml_data"]))
        return config_path

    monkeypatch.setattr(
        "tokenpal.config.cloud_writer.update_config", fake_update_config
    )
    return state


@pytest.fixture()
def cfg() -> TokenPalConfig:
    return TokenPalConfig(cloud_llm=CloudLLMConfig())


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def test_status_disabled_no_key(isolated, cfg: TokenPalConfig) -> None:
    result = _handle_cloud_command("", cfg)
    assert "disabled" in result.message.lower()
    assert "key stored" not in result.message.lower()


def test_status_subcommand_alias(isolated, cfg: TokenPalConfig) -> None:
    assert _handle_cloud_command("status", cfg).message == \
        _handle_cloud_command("", cfg).message


def test_status_enabled_no_key(isolated, cfg: TokenPalConfig) -> None:
    cfg.cloud_llm.enabled = True
    msg = _handle_cloud_command("", cfg).message.lower()
    assert "enabled but no key" in msg


def test_status_enabled_with_key_shows_fingerprint(
    isolated, cfg: TokenPalConfig
) -> None:
    _handle_cloud_command("enable sk-ant-api03-" + "a" * 40, cfg)
    msg = _handle_cloud_command("", cfg).message
    assert "enabled" in msg.lower()
    assert "sk-ant-..." in msg
    # Fingerprint shows last 4 chars only
    assert "aaaa" in msg


# ---------------------------------------------------------------------------
# Enable
# ---------------------------------------------------------------------------


def test_enable_requires_key_arg(isolated, cfg: TokenPalConfig) -> None:
    msg = _handle_cloud_command("enable", cfg).message
    assert "Usage:" in msg
    assert "console.anthropic.com" in msg


def test_enable_rejects_bad_shape(isolated, cfg: TokenPalConfig) -> None:
    msg = _handle_cloud_command("enable not-a-real-key", cfg).message
    assert "rejected" in msg.lower()
    assert cfg.cloud_llm.enabled is False


def test_enable_stores_key_and_flips_config(
    isolated, cfg: TokenPalConfig
) -> None:
    key = "sk-ant-api03-" + "b" * 40
    result = _handle_cloud_command(f"enable {key}", cfg)
    # Config live-flipped
    assert cfg.cloud_llm.enabled is True
    # Persisted to TOML
    assert isolated["toml_data"]["cloud_llm"]["enabled"] is True
    # Key on disk
    stored = json.loads(isolated["secrets_path"].read_text())
    assert stored["cloud_key"] == key
    # Fingerprint in message, never raw key
    assert key not in result.message, "raw key must never echo back"
    assert "sk-ant-..." in result.message


def test_enable_persists_key_at_0o600(isolated, cfg: TokenPalConfig) -> None:
    key = "sk-ant-api03-" + "c" * 40
    _handle_cloud_command(f"enable {key}", cfg)
    mode = stat.S_IMODE(isolated["secrets_path"].stat().st_mode)
    assert mode == 0o600


def test_enable_scrubs_raw_key_from_all_returned_fields(
    isolated, cfg: TokenPalConfig
) -> None:
    """Defense-in-depth: scan the whole CommandResult for the raw key."""
    key = "sk-ant-api03-" + "d" * 40
    result = _handle_cloud_command(f"enable {key}", cfg)
    for field in (result.message, result.error or ""):
        assert key not in field, f"raw key leaked via {field!r}"


# ---------------------------------------------------------------------------
# Disable
# ---------------------------------------------------------------------------


def test_disable_flips_off_keeps_key(isolated, cfg: TokenPalConfig) -> None:
    _handle_cloud_command("enable sk-ant-api03-" + "e" * 40, cfg)
    assert cfg.cloud_llm.enabled is True
    msg = _handle_cloud_command("disable", cfg).message
    assert "disabled" in msg.lower()
    assert "retained" in msg.lower()
    assert cfg.cloud_llm.enabled is False
    # Key still on disk
    stored = json.loads(isolated["secrets_path"].read_text())
    assert "cloud_key" in stored


def test_disable_without_key_no_retained_suffix(
    isolated, cfg: TokenPalConfig
) -> None:
    msg = _handle_cloud_command("disable", cfg).message
    assert "retained" not in msg.lower()


# ---------------------------------------------------------------------------
# Forget
# ---------------------------------------------------------------------------


def test_forget_wipes_key_and_disables(isolated, cfg: TokenPalConfig) -> None:
    _handle_cloud_command("enable sk-ant-api03-" + "f" * 40, cfg)
    msg = _handle_cloud_command("forget", cfg).message
    assert "wiped" in msg.lower()
    assert cfg.cloud_llm.enabled is False
    # Key should be absent from .secrets.json
    if isolated["secrets_path"].exists():
        stored = json.loads(isolated["secrets_path"].read_text())
        assert "cloud_key" not in stored


def test_forget_is_idempotent_when_empty(isolated, cfg: TokenPalConfig) -> None:
    # Should not raise even with no key + no config
    msg = _handle_cloud_command("forget", cfg).message
    assert "wiped" in msg.lower()


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


def test_model_lists_allowlist_when_no_arg(isolated, cfg: TokenPalConfig) -> None:
    msg = _handle_cloud_command("model", cfg).message
    assert "claude-haiku-4-5" in msg
    assert "claude-sonnet-4-6" in msg
    assert "claude-opus-4-7" in msg


def test_model_rejects_unknown(isolated, cfg: TokenPalConfig) -> None:
    msg = _handle_cloud_command("model claude-opus-3", cfg).message
    assert "Unknown model" in msg
    # Config unchanged
    assert cfg.cloud_llm.model == "claude-haiku-4-5"


def test_model_accepts_allowlisted(isolated, cfg: TokenPalConfig) -> None:
    msg = _handle_cloud_command("model claude-sonnet-4-6", cfg).message
    assert "set to" in msg
    assert cfg.cloud_llm.model == "claude-sonnet-4-6"
    assert isolated["toml_data"]["cloud_llm"]["model"] == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Unknown subcommand
# ---------------------------------------------------------------------------


def test_unknown_subcommand_returns_usage(isolated, cfg: TokenPalConfig) -> None:
    msg = _handle_cloud_command("nonsense", cfg).message
    assert "Usage:" in msg
