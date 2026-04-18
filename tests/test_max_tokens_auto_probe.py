"""Tests for auto-derived max_tokens from server capability (GH #30)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from tokenpal.llm.http_backend import HttpBackend


def _backend(
    api_url: str = "http://localhost:11434/v1",
    per_server_max_tokens: dict[str, int] | None = None,
    max_tokens: int = 60,
    inference_engine: str = "ollama",
) -> HttpBackend:
    config: dict[str, Any] = {
        "api_url": api_url,
        "model_name": "gemma4",
        "max_tokens": max_tokens,
        "per_server_max_tokens": per_server_max_tokens or {},
        "inference_engine": inference_engine,
    }
    return HttpBackend(config)


async def _apply_with_probe(backend: HttpBackend, probe_return: int | None) -> None:
    backend._probe_context_length = AsyncMock(return_value=probe_return)  # type: ignore[method-assign]
    await backend._apply_auto_max_tokens()


@pytest.mark.asyncio
async def test_derives_from_small_context() -> None:
    b = _backend()
    await _apply_with_probe(b, 1024)
    assert b.context_length == 1024
    assert b.derived_max_tokens == 256
    assert b.max_tokens == 256


@pytest.mark.asyncio
async def test_derives_capped_at_ceiling() -> None:
    b = _backend()
    await _apply_with_probe(b, 65536)
    assert b.derived_max_tokens == 1024
    assert b.max_tokens == 1024


@pytest.mark.asyncio
async def test_probe_failure_leaves_max_tokens_untouched() -> None:
    b = _backend(max_tokens=60)
    await _apply_with_probe(b, None)
    assert b.context_length is None
    assert b.derived_max_tokens is None
    assert b.max_tokens == 60


@pytest.mark.asyncio
async def test_user_pin_wins_over_probe() -> None:
    b = _backend(per_server_max_tokens={"http://localhost:11434/v1": 99})
    assert b._max_tokens_pinned is True
    assert b.max_tokens == 99
    await _apply_with_probe(b, 8192)
    # Probe populated derived field, but pinned max_tokens unchanged.
    assert b.derived_max_tokens == 1024
    assert b.max_tokens == 99


def test_set_api_url_re_evaluates_pin() -> None:
    b = _backend(
        api_url="http://a:1/v1",
        per_server_max_tokens={"http://b:2/v1": 123},
        max_tokens=60,
    )
    # Not pinned initially (current URL not in dict).
    assert b._max_tokens_pinned is False
    assert b.max_tokens == 60

    b.set_api_url("http://b:2/v1")
    assert b._max_tokens_pinned is True
    assert b.max_tokens == 123

    b.set_api_url("http://c:3/v1")
    assert b._max_tokens_pinned is False
    assert b.max_tokens == 60


def test_set_model_clears_derived_probe() -> None:
    b = _backend()
    b._derived_max_tokens = 256
    b._context_length = 2048
    b._max_tokens = 256

    b.set_model("other-model")
    assert b.derived_max_tokens is None
    assert b.context_length is None
    # Reset to initial when not pinned.
    assert b.max_tokens == 60


def test_set_model_preserves_pinned_max_tokens() -> None:
    b = _backend(per_server_max_tokens={"http://localhost:11434/v1": 99})
    b.set_model("other-model")
    assert b.max_tokens == 99
    assert b._max_tokens_pinned is True


@pytest.mark.asyncio
async def test_probe_parses_ollama_api_show_response() -> None:
    """_probe_context_length parses model_info[*.context_length]."""
    b = _backend()

    class FakeResp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "model_info": {
                    "general.architecture": "gemma",
                    "gemma.context_length": 8192,
                    "gemma.embedding_length": 2048,
                }
            }

    client = AsyncMock()
    client.post = AsyncMock(return_value=FakeResp())
    b._client = client  # type: ignore[assignment]
    ctx = await b._probe_context_length()
    assert ctx == 8192
    call_kwargs = client.post.await_args.kwargs
    # URL should be native (no /v1).
    assert client.post.await_args.args[0] == "http://localhost:11434/api/show"
    assert call_kwargs["json"] == {"name": "gemma4"}


@pytest.mark.asyncio
async def test_probe_returns_none_on_http_error() -> None:
    b = _backend()
    client = AsyncMock()
    client.post = AsyncMock(side_effect=httpx.ConnectError("nope"))
    b._client = client  # type: ignore[assignment]
    assert await b._probe_context_length() is None


@pytest.mark.asyncio
async def test_probe_returns_none_when_model_info_missing() -> None:
    b = _backend()

    class FakeResp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"details": {}}  # no model_info

    client = AsyncMock()
    client.post = AsyncMock(return_value=FakeResp())
    b._client = client  # type: ignore[assignment]
    assert await b._probe_context_length() is None


def test_set_max_tokens_marks_pinned() -> None:
    b = _backend()
    assert b._max_tokens_pinned is False
    b.set_max_tokens(200)
    assert b._max_tokens_pinned is True
    assert b.max_tokens == 200


@pytest.mark.asyncio
async def test_props_probe_parses_llamacpp_response() -> None:
    """_probe_llamacpp_props reads default_generation_settings.n_ctx."""
    b = _backend(inference_engine="llamacpp")

    class FakeResp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "default_generation_settings": {
                    "n_ctx": 8192,
                    "params": {"n_predict": -1},
                },
                "model_path": "/models/qwen3.gguf",
            }

    client = AsyncMock()
    client.get = AsyncMock(return_value=FakeResp())
    b._client = client  # type: ignore[assignment]
    ctx = await b._probe_llamacpp_props()
    assert ctx == 8192
    # URL should be native (no /v1), GET not POST.
    assert client.get.await_args.args[0] == "http://localhost:11434/props"


@pytest.mark.asyncio
async def test_props_probe_returns_none_on_http_error() -> None:
    b = _backend(inference_engine="llamacpp")
    client = AsyncMock()
    client.get = AsyncMock(side_effect=httpx.ConnectError("nope"))
    b._client = client  # type: ignore[assignment]
    assert await b._probe_llamacpp_props() is None


@pytest.mark.asyncio
async def test_props_probe_returns_none_when_n_ctx_missing() -> None:
    b = _backend(inference_engine="llamacpp")

    class FakeResp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"default_generation_settings": {}}

    client = AsyncMock()
    client.get = AsyncMock(return_value=FakeResp())
    b._client = client  # type: ignore[assignment]
    assert await b._probe_llamacpp_props() is None


@pytest.mark.asyncio
async def test_llamacpp_engine_routes_to_props_probe() -> None:
    """_apply_auto_max_tokens uses /props for llamacpp, not /api/show."""
    b = _backend(inference_engine="llamacpp")
    b._probe_llamacpp_props = AsyncMock(return_value=4096)  # type: ignore[method-assign]
    b._probe_context_length = AsyncMock(return_value=None)  # type: ignore[method-assign]
    await b._apply_auto_max_tokens()
    assert b.context_length == 4096
    assert b.derived_max_tokens == 1024  # min(4096//4, hard cap)
    b._probe_llamacpp_props.assert_awaited_once()
    b._probe_context_length.assert_not_awaited()
