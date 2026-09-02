"""Tests for the shared backend config-dict builder."""

from __future__ import annotations

from tokenpal.config.schema import TokenPalConfig
from tokenpal.llm.registry import backend_config


def test_backend_config_carries_llm_fields_and_server_mode():
    config = TokenPalConfig()
    config.llm.model_name = "qwen3-27b"
    config.server.mode = "remote"

    built = backend_config(config)

    assert built["model_name"] == "qwen3-27b"
    assert built["api_url"] == config.llm.api_url
    assert built["server_mode"] == "remote"
    assert "memory_store" not in built


def test_backend_config_includes_memory_store_only_when_passed():
    config = TokenPalConfig()
    sentinel = object()

    assert backend_config(config, memory_store=sentinel)["memory_store"] is sentinel
    assert "memory_store" not in backend_config(config, memory_store=None)


def test_backend_config_overrides_win():
    config = TokenPalConfig()
    config.llm.temperature = 0.8
    config.server.mode = "auto"

    built = backend_config(config, temperature=0.3, server_mode="local", request_timeout_s=120.0)

    assert built["temperature"] == 0.3
    assert built["server_mode"] == "local"
    assert built["request_timeout_s"] == 120.0
