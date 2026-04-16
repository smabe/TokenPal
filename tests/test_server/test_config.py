"""Tests for ServerConfig integration into the config system."""

from tokenpal.config.schema import LLMConfig, ServerConfig, TokenPalConfig


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


def test_llm_inference_engine_default_is_ollama():
    cfg = LLMConfig()
    assert cfg.inference_engine == "ollama"


def test_llm_inference_engine_switches_to_llamacpp():
    cfg = LLMConfig(inference_engine="llamacpp")
    assert cfg.inference_engine == "llamacpp"


def test_inference_engine_round_trips_through_tokenpal_config():
    cfg = TokenPalConfig(llm=LLMConfig(inference_engine="llamacpp"))
    assert cfg.llm.inference_engine == "llamacpp"
