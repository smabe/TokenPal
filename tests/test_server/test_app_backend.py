"""Tests for the dual-backend server — inference_url/inference_engine plumbing."""

from __future__ import annotations

from tokenpal.server.app import create_app


def test_create_app_accepts_inference_url():
    app = create_app(inference_url="http://fake:11434")
    assert app.state.inference_url == "http://fake:11434"
    assert app.state.ollama_url == "http://fake:11434"  # back-compat alias
    assert app.state.inference_engine == "ollama"


def test_create_app_ollama_url_alias_still_works():
    """Deprecated alias ollama_url must keep working for one release."""
    app = create_app(ollama_url="http://legacy:11434")
    assert app.state.inference_url == "http://legacy:11434"
    assert app.state.ollama_url == "http://legacy:11434"


def test_create_app_inference_url_wins_over_alias():
    """When both are passed, inference_url is authoritative."""
    app = create_app(
        inference_url="http://new:11434",
        ollama_url="http://old:11434",
    )
    assert app.state.inference_url == "http://new:11434"
    assert app.state.ollama_url == "http://new:11434"


def test_create_app_llamacpp_engine_flag():
    app = create_app(
        inference_url="http://localhost:11434",
        inference_engine="llamacpp",
    )
    assert app.state.inference_engine == "llamacpp"
