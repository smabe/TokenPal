"""Tests for auto-adopting the server's model on connect."""

from __future__ import annotations

import httpx
import pytest

from tokenpal.llm.http_backend import HttpBackend


def _make_backend(
    model_name: str = "gemma4",
    per_server_models: dict | None = None,
) -> HttpBackend:
    cfg: dict = {
        "api_url": "http://fake-server:8585/v1",
        "model_name": model_name,
    }
    if per_server_models:
        cfg["per_server_models"] = per_server_models
    return cfg, HttpBackend(cfg)


def _mock_transport(models: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        if "/models" in str(request.url):
            return httpx.Response(200, json={
                "data": [{"id": m} for m in models],
            })
        return httpx.Response(404)
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_auto_adopts_server_model_when_no_override():
    _, backend = _make_backend(model_name="gemma4")
    backend._client = httpx.AsyncClient(transport=_mock_transport(["Qwen3-14B-Q4_K_M"]))

    result = await backend._try_connect("http://fake-server:8585/v1")

    assert result is True
    assert backend._model_name == "Qwen3-14B-Q4_K_M"
    assert backend._model_available is True


@pytest.mark.asyncio
async def test_keeps_configured_model_when_server_has_it():
    _, backend = _make_backend(model_name="gemma4")
    backend._client = httpx.AsyncClient(transport=_mock_transport(["gemma4", "phi4"]))

    await backend._try_connect("http://fake-server:8585/v1")

    assert backend._model_name == "gemma4"
    assert backend._model_available is True


@pytest.mark.asyncio
async def test_respects_per_server_override():
    """When user has pinned a model for this server, don't auto-adopt."""
    _, backend = _make_backend(
        model_name="my-pinned-model",
        per_server_models={"http://fake-server:8585/v1": "my-pinned-model"},
    )
    backend._client = httpx.AsyncClient(transport=_mock_transport(["Qwen3-14B-Q4_K_M"]))

    await backend._try_connect("http://fake-server:8585/v1")

    assert backend._model_name == "my-pinned-model"
    assert backend._model_available is False


@pytest.mark.asyncio
async def test_handles_empty_model_list():
    _, backend = _make_backend(model_name="gemma4")
    backend._client = httpx.AsyncClient(transport=_mock_transport([]))

    result = await backend._try_connect("http://fake-server:8585/v1")

    assert result is True
    assert backend._model_name == "gemma4"


@pytest.mark.asyncio
async def test_no_adopt_on_fallback_path():
    """Fallback to local Ollama should not auto-adopt random models."""
    _, backend = _make_backend(model_name="gemma4")
    backend._client = httpx.AsyncClient(
        transport=_mock_transport(["tokenpal-bmo:latest"]),
    )

    await backend._try_connect(
        "http://localhost:11434/v1", allow_adopt=False,
    )

    assert backend._model_name == "gemma4"
    assert backend._model_available is False
