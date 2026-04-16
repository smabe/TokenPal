"""Tests for /model slash command backend gating (ollama vs llamacpp)."""

from __future__ import annotations

from unittest.mock import MagicMock

from tokenpal.app import _handle_model_command
from tokenpal.config.schema import LLMConfig, TokenPalConfig


def _cfg(engine: str) -> TokenPalConfig:
    return TokenPalConfig(llm=LLMConfig(inference_engine=engine))


def _mock_llm() -> MagicMock:
    llm = MagicMock()
    llm.model_name = "gemma4"
    llm.api_url = "http://localhost:11434/v1"
    return llm


def test_model_pull_disabled_on_llamacpp():
    result = _handle_model_command(
        "pull gemma4", _mock_llm(), MagicMock(), brain=None, config=_cfg("llamacpp"),
    )
    assert "docs/amd-dgpu-setup.md" in result.message


def test_model_browse_disabled_on_llamacpp():
    result = _handle_model_command(
        "browse", _mock_llm(), MagicMock(), brain=None, config=_cfg("llamacpp"),
    )
    assert "docs/amd-dgpu-setup.md" in result.message


def test_model_list_disabled_on_llamacpp():
    result = _handle_model_command(
        "list", _mock_llm(), MagicMock(), brain=None, config=_cfg("llamacpp"),
    )
    assert "docs/amd-dgpu-setup.md" in result.message


def test_model_pull_usage_error_on_ollama():
    """Ollama path still reaches the existing handler — usage error proves the gate opens."""
    result = _handle_model_command(
        "pull", _mock_llm(), MagicMock(), brain=None, config=_cfg("ollama"),
    )
    assert "Usage" in result.message
