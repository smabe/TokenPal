"""Tests for ServerConfig integration into the config system."""

from tokenpal.config.schema import ServerConfig, TokenPalConfig


def test_server_config_defaults():
    cfg = ServerConfig()
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8585
    assert cfg.mode == "auto"
    assert cfg.auth_backend == "none"
    assert cfg.api_key == ""
    assert cfg.ollama_url == "http://localhost:11434"


def test_server_config_in_tokenpal_config():
    cfg = TokenPalConfig()
    assert isinstance(cfg.server, ServerConfig)
    assert cfg.server.port == 8585


def test_server_config_custom_values():
    cfg = ServerConfig(host="0.0.0.0", port=9999, mode="remote")
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 9999
    assert cfg.mode == "remote"
