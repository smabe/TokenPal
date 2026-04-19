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


def test_enable_requires_key_arg_when_none_stored(
    isolated, cfg: TokenPalConfig
) -> None:
    msg = _handle_cloud_command("enable", cfg).message
    assert "Usage:" in msg
    assert "console.anthropic.com" in msg


def test_bare_enable_re_enables_when_key_already_stored(
    isolated, cfg: TokenPalConfig
) -> None:
    # First enable stores + flips on; disable flips off but retains key.
    key = "sk-ant-api03-" + "r" * 40
    _handle_cloud_command(f"enable {key}", cfg)
    _handle_cloud_command("disable", cfg)
    assert cfg.cloud_llm.enabled is False
    # Bare "enable" should flip back on using the stored key (no key re-paste).
    result = _handle_cloud_command("enable", cfg)
    assert cfg.cloud_llm.enabled is True
    assert "enabled" in result.message.lower()
    # Fingerprint of the stored key appears in status.
    assert key[-4:] in result.message


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
    # Key on disk (stored under the current `anthropic_key` field)
    stored = json.loads(isolated["secrets_path"].read_text())
    assert stored["anthropic_key"] == key
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
    # Key still on disk (under the current field name)
    stored = json.loads(isolated["secrets_path"].read_text())
    assert "anthropic_key" in stored


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


# ---------------------------------------------------------------------------
# Deep mode (/cloud deep on|off)
# ---------------------------------------------------------------------------


def test_deep_rejected_on_haiku_model(isolated, cfg: TokenPalConfig) -> None:
    # Default model is Haiku — deep should refuse until the user changes it.
    assert cfg.cloud_llm.model == "claude-haiku-4-5"
    msg = _handle_cloud_command("deep on", cfg).message
    assert "requires" in msg.lower()
    assert cfg.cloud_llm.research_deep is False


def test_deep_on_persists_for_sonnet(isolated, cfg: TokenPalConfig) -> None:
    _handle_cloud_command("model claude-sonnet-4-6", cfg)
    msg = _handle_cloud_command("deep on", cfg).message
    assert "on" in msg.lower()
    assert cfg.cloud_llm.research_deep is True
    assert isolated["toml_data"]["cloud_llm"]["research_deep"] is True


def test_deep_off_clears_flag(isolated, cfg: TokenPalConfig) -> None:
    _handle_cloud_command("model claude-opus-4-7", cfg)
    _handle_cloud_command("deep on", cfg)
    msg = _handle_cloud_command("deep off", cfg).message
    assert "off" in msg.lower()
    assert cfg.cloud_llm.research_deep is False


def test_deep_bare_status(isolated, cfg: TokenPalConfig) -> None:
    _handle_cloud_command("model claude-sonnet-4-6", cfg)
    _handle_cloud_command("deep on", cfg)
    msg = _handle_cloud_command("deep", cfg).message
    assert "on" in msg.lower()


def test_status_surfaces_deep_flag(isolated, cfg: TokenPalConfig) -> None:
    _handle_cloud_command("enable sk-ant-api03-" + "d" * 40, cfg)
    _handle_cloud_command("model claude-sonnet-4-6", cfg)
    _handle_cloud_command("deep on", cfg)
    msg = _handle_cloud_command("", cfg).message
    assert "deep" in msg.lower()


def test_deep_on_returns_cost_warning(isolated, cfg: TokenPalConfig) -> None:
    _handle_cloud_command("model claude-sonnet-4-6", cfg)
    msg = _handle_cloud_command("deep on", cfg).message
    # Must warn about cost on activation, not just after-the-fact.
    assert "warning" in msg.lower() or "$" in msg


def test_search_rejected_on_haiku(isolated, cfg: TokenPalConfig) -> None:
    msg = _handle_cloud_command("search on", cfg).message
    assert "requires" in msg.lower()
    assert cfg.cloud_llm.research_search is False


def test_search_on_persists_for_sonnet(isolated, cfg: TokenPalConfig) -> None:
    _handle_cloud_command("model claude-sonnet-4-6", cfg)
    msg = _handle_cloud_command("search on", cfg).message
    assert "on" in msg.lower()
    assert cfg.cloud_llm.research_search is True
    assert isolated["toml_data"]["cloud_llm"]["research_search"] is True


def test_search_off_clears_flag(isolated, cfg: TokenPalConfig) -> None:
    _handle_cloud_command("model claude-sonnet-4-6", cfg)
    _handle_cloud_command("search on", cfg)
    msg = _handle_cloud_command("search off", cfg).message
    assert "off" in msg.lower()
    assert cfg.cloud_llm.research_search is False


def test_search_on_while_deep_on_notes_override(
    isolated, cfg: TokenPalConfig
) -> None:
    _handle_cloud_command("model claude-sonnet-4-6", cfg)
    _handle_cloud_command("deep on", cfg)
    msg = _handle_cloud_command("search on", cfg).message
    assert "deep" in msg.lower() and "precedence" in msg.lower()


def test_status_surfaces_search_flag(isolated, cfg: TokenPalConfig) -> None:
    _handle_cloud_command("enable sk-ant-api03-" + "s" * 40, cfg)
    _handle_cloud_command("model claude-sonnet-4-6", cfg)
    _handle_cloud_command("search on", cfg)
    msg = _handle_cloud_command("", cfg).message
    assert "search" in msg.lower()


# ---------------------------------------------------------------------------
# Two-level dispatch: /cloud tavily
# ---------------------------------------------------------------------------


def test_tavily_status_disabled_no_key(isolated, cfg: TokenPalConfig) -> None:
    msg = _handle_cloud_command("tavily status", cfg).message.lower()
    assert "tavily" in msg
    assert "disabled" in msg


def test_tavily_enable_stores_key_and_flips_config(
    isolated, cfg: TokenPalConfig
) -> None:
    key = "tvly-" + "a" * 32
    result = _handle_cloud_command(f"tavily enable {key}", cfg)
    assert cfg.cloud_search.enabled is True
    assert isolated["toml_data"]["cloud_search"]["enabled"] is True
    stored = json.loads(isolated["secrets_path"].read_text())
    assert stored["tavily_key"] == key
    assert key not in result.message, "raw tavily key must never echo back"
    assert "tvly-..." in result.message


def test_tavily_disable_flips_off_keeps_key(isolated, cfg: TokenPalConfig) -> None:
    _handle_cloud_command("tavily enable tvly-" + "b" * 32, cfg)
    assert cfg.cloud_search.enabled is True
    msg = _handle_cloud_command("tavily disable", cfg).message.lower()
    assert "disabled" in msg
    assert cfg.cloud_search.enabled is False
    stored = json.loads(isolated["secrets_path"].read_text())
    assert "tavily_key" in stored


def test_tavily_forget_wipes_key_and_disables(
    isolated, cfg: TokenPalConfig
) -> None:
    _handle_cloud_command("tavily enable tvly-" + "c" * 32, cfg)
    _handle_cloud_command("tavily forget", cfg)
    assert cfg.cloud_search.enabled is False
    stored = json.loads(isolated["secrets_path"].read_text())
    assert "tavily_key" not in stored


# ---------------------------------------------------------------------------
# Two-level dispatch: /cloud brave
# ---------------------------------------------------------------------------


def test_brave_status_no_key(isolated, cfg: TokenPalConfig) -> None:
    msg = _handle_cloud_command("brave status", cfg).message.lower()
    assert "brave" in msg
    assert "disabled" in msg
    assert "no key" in msg


def test_brave_enable_stores_key(isolated, cfg: TokenPalConfig) -> None:
    key = "BSA-" + "x" * 28
    result = _handle_cloud_command(f"brave enable {key}", cfg)
    stored = json.loads(isolated["secrets_path"].read_text())
    assert stored["brave_key"] == key
    assert key not in result.message, "raw brave key must never echo back"


def test_brave_enable_rejects_too_short(isolated, cfg: TokenPalConfig) -> None:
    result = _handle_cloud_command("brave enable short", cfg)
    assert "rejected" in result.message.lower()


def test_brave_forget_wipes_key(isolated, cfg: TokenPalConfig) -> None:
    _handle_cloud_command("brave enable BSA-" + "y" * 28, cfg)
    _handle_cloud_command("brave forget", cfg)
    stored = json.loads(isolated["secrets_path"].read_text()) if isolated["secrets_path"].exists() else {}
    assert "brave_key" not in stored


def test_brave_status_with_key_shows_fingerprint(
    isolated, cfg: TokenPalConfig
) -> None:
    _handle_cloud_command("brave enable BSA-" + "z" * 28, cfg)
    msg = _handle_cloud_command("brave status", cfg).message
    assert "..." in msg  # fingerprint tail marker
    assert "key on disk" in msg.lower()


# ---------------------------------------------------------------------------
# Aggregate status includes all three backends
# ---------------------------------------------------------------------------


def test_aggregate_status_lists_all_backends(
    isolated, cfg: TokenPalConfig
) -> None:
    msg = _handle_cloud_command("", cfg).message
    assert "Anthropic:" in msg
    assert "Tavily:" in msg
    assert "Brave:" in msg


# ---------------------------------------------------------------------------
# Legacy flat subcommands still work (back-compat)
# ---------------------------------------------------------------------------


def test_legacy_enable_routes_to_anthropic(isolated, cfg: TokenPalConfig) -> None:
    """Pre-refactor users type `/cloud enable <key>`, not `/cloud anthropic
    enable <key>`. The legacy form must continue to work."""
    key = "sk-ant-api03-" + "q" * 40
    _handle_cloud_command(f"enable {key}", cfg)
    assert cfg.cloud_llm.enabled is True
    stored = json.loads(isolated["secrets_path"].read_text())
    assert stored["anthropic_key"] == key


def test_legacy_model_subcommand_still_works(
    isolated, cfg: TokenPalConfig
) -> None:
    _handle_cloud_command("enable sk-ant-api03-" + "r" * 40, cfg)
    _handle_cloud_command("model claude-sonnet-4-6", cfg)
    assert cfg.cloud_llm.model == "claude-sonnet-4-6"
