"""Tests for set_api_url() on LLM backends."""

import pytest

from tokenpal.llm.base import AbstractLLMBackend
from tokenpal.llm.http_backend import HttpBackend


def test_set_api_url_changes_endpoint():
    backend = HttpBackend({"api_url": "http://localhost:11434/v1"})
    assert backend._api_url == "http://localhost:11434/v1"

    backend.set_api_url("http://geefourteen:8585/v1")
    assert backend._api_url == "http://geefourteen:8585/v1"


def test_set_api_url_resets_state():
    backend = HttpBackend({"api_url": "http://localhost:11434/v1"})
    backend._reachable = True
    backend._model_available = True

    backend.set_api_url("http://geefourteen:8585/v1")
    assert backend._reachable is False
    assert backend._model_available is False


def test_set_api_url_strips_trailing_slash():
    backend = HttpBackend({"api_url": "http://localhost:11434/v1"})
    backend.set_api_url("http://geefourteen:8585/v1/")
    assert backend._api_url == "http://geefourteen:8585/v1"


def test_abstract_backend_raises_not_implemented():
    class DummyBackend(AbstractLLMBackend):
        backend_name = "dummy"
        platforms = ("darwin",)
        async def setup(self): pass
        async def generate(self, prompt, max_tokens=256, **_): pass
        async def teardown(self): pass

    backend = DummyBackend({})
    with pytest.raises(NotImplementedError, match="does not support URL switching"):
        backend.set_api_url("http://example.com")


def test_llamacpp_dispatch_sends_chat_template_kwargs():
    """llamacpp backend always sends enable_thinking explicitly + reasoning_format=deepseek."""
    backend = HttpBackend({
        "api_url": "http://localhost:11434/v1",
        "inference_engine": "llamacpp",
        "disable_reasoning": True,
    })

    body: dict = {}
    backend._apply_thinking_controls(body, enable_thinking=None)
    assert body["chat_template_kwargs"] == {"enable_thinking": "false"}
    assert body["reasoning_format"] == "deepseek"
    assert "reasoning_effort" not in body

    body = {}
    backend._apply_thinking_controls(body, enable_thinking=True)
    assert body["chat_template_kwargs"] == {"enable_thinking": "true"}

    body = {}
    backend._apply_thinking_controls(body, enable_thinking=False)
    assert body["chat_template_kwargs"] == {"enable_thinking": "false"}


def test_llamacpp_dispatch_respects_backend_default_when_disable_reasoning_false():
    backend = HttpBackend({
        "api_url": "http://localhost:11434/v1",
        "inference_engine": "llamacpp",
        "disable_reasoning": False,
    })
    body: dict = {}
    backend._apply_thinking_controls(body, enable_thinking=None)
    assert body["chat_template_kwargs"] == {"enable_thinking": "true"}


def test_ollama_dispatch_sends_reasoning_effort():
    backend = HttpBackend({
        "api_url": "http://localhost:11434/v1",
        "inference_engine": "ollama",
        "disable_reasoning": True,
    })

    body: dict = {}
    backend._apply_thinking_controls(body, enable_thinking=None)
    assert body["reasoning_effort"] == "none"
    assert "chat_template_kwargs" not in body
    assert "reasoning_format" not in body

    body = {}
    backend._apply_thinking_controls(body, enable_thinking=True)
    assert body["reasoning_effort"] == "high"

    body = {}
    backend._apply_thinking_controls(body, enable_thinking=False)
    assert body["reasoning_effort"] == "none"


def test_ollama_default_engine_when_unset():
    """Config dicts without inference_engine fall back to ollama (matches LLMConfig default)."""
    backend = HttpBackend({"api_url": "http://localhost:11434/v1"})
    body: dict = {}
    backend._apply_thinking_controls(body, enable_thinking=None)
    assert "reasoning_effort" in body
    assert "chat_template_kwargs" not in body


@pytest.mark.asyncio
async def test_generate_passes_response_format_to_body(monkeypatch):
    """response_format kwarg is forwarded to the OpenAI-compat request body."""
    import httpx

    backend = HttpBackend({
        "api_url": "http://localhost:11434/v1",
        "inference_engine": "llamacpp",
    })
    captured: dict = {}

    class _FakeResponse:
        def raise_for_status(self): pass
        def json(self):
            return {
                "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                "usage": {"total_tokens": 1},
            }

    class _FakeClient:
        async def post(self, url, json):
            captured["body"] = json
            return _FakeResponse()
        async def aclose(self): pass

    backend._client = _FakeClient()  # type: ignore[assignment]
    schema = {"type": "object", "properties": {"k": {"type": "string"}}}
    await backend.generate(
        "hello",
        max_tokens=10,
        response_format={"type": "json_schema", "schema": schema},
    )
    assert captured["body"]["response_format"] == {
        "type": "json_schema",
        "schema": schema,
    }
    _ = httpx  # silence unused-import linter if enabled
