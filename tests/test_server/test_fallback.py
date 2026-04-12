"""Tests for auto-fallback to local Ollama when server is unreachable."""

from unittest.mock import patch

import httpx

from tokenpal.llm.http_backend import HttpBackend


def _models_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"data": [{"id": "gemma4"}]})


def _always_refuse(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("Connection refused")


def _remote_fails_local_ok(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "geefourteen" in url:
        raise httpx.ConnectError("Connection refused")
    if "localhost" in url or "127.0.0.1" in url:
        return httpx.Response(200, json={"data": [{"id": "gemma4"}]})
    return httpx.Response(404)


async def test_fallback_to_local_when_remote_unreachable():
    backend = HttpBackend({
        "api_url": "http://geefourteen:8585/v1",
        "model_name": "gemma4",
        "server_mode": "auto",
    })
    with patch.object(
        httpx, "AsyncClient",
        return_value=httpx.AsyncClient(transport=httpx.MockTransport(_remote_fails_local_ok)),
    ):
        await backend.setup()

    assert backend.is_reachable
    assert backend.using_fallback
    assert backend.api_url == "http://localhost:11434/v1"


async def test_no_fallback_when_remote_reachable():
    backend = HttpBackend({
        "api_url": "http://geefourteen:8585/v1",
        "model_name": "gemma4",
        "server_mode": "auto",
    })
    with patch.object(
        httpx, "AsyncClient",
        return_value=httpx.AsyncClient(transport=httpx.MockTransport(_models_ok)),
    ):
        await backend.setup()

    assert backend.is_reachable
    assert not backend.using_fallback
    assert "geefourteen" in backend.api_url


async def test_no_fallback_when_mode_remote():
    backend = HttpBackend({
        "api_url": "http://geefourteen:8585/v1",
        "model_name": "gemma4",
        "server_mode": "remote",
    })
    with patch.object(
        httpx, "AsyncClient",
        return_value=httpx.AsyncClient(transport=httpx.MockTransport(_always_refuse)),
    ):
        await backend.setup()

    assert not backend.is_reachable
    assert not backend.using_fallback


async def test_no_fallback_when_already_local():
    backend = HttpBackend({
        "api_url": "http://localhost:11434/v1",
        "model_name": "gemma4",
        "server_mode": "auto",
    })
    with patch.object(
        httpx, "AsyncClient",
        return_value=httpx.AsyncClient(transport=httpx.MockTransport(_always_refuse)),
    ):
        await backend.setup()

    assert not backend.is_reachable
    assert not backend.using_fallback


async def test_primary_url_preserved_after_fallback():
    backend = HttpBackend({
        "api_url": "http://geefourteen:8585/v1",
        "model_name": "gemma4",
        "server_mode": "auto",
    })
    with patch.object(
        httpx, "AsyncClient",
        return_value=httpx.AsyncClient(transport=httpx.MockTransport(_remote_fails_local_ok)),
    ):
        await backend.setup()

    assert backend._primary_url == "http://geefourteen:8585/v1"
    assert backend.api_url == "http://localhost:11434/v1"
