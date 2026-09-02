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
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["reasoning_format"] == "deepseek"
    assert "reasoning_effort" not in body

    body = {}
    backend._apply_thinking_controls(body, enable_thinking=True)
    assert body["chat_template_kwargs"] == {"enable_thinking": True}

    body = {}
    backend._apply_thinking_controls(body, enable_thinking=False)
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


@pytest.mark.parametrize(
    ("engine", "enable", "effort", "expected"),
    [
        ("llamacpp", True, "low", "low"),
        ("llamacpp", False, "low", None),
        ("llamacpp", True, None, None),
        ("ollama", True, "low", "low"),
        ("ollama", False, "low", "none"),
        ("ollama", True, None, "high"),
    ],
)
def test_thinking_effort_written_per_engine(engine, enable, effort, expected):
    backend = HttpBackend({
        "api_url": "http://localhost:11434/v1",
        "inference_engine": engine,
    })
    body: dict = {}
    backend._apply_thinking_controls(body, enable_thinking=enable, thinking_effort=effort)
    assert body.get("reasoning_effort") == expected


def test_llamacpp_dispatch_respects_backend_default_when_disable_reasoning_false():
    backend = HttpBackend({
        "api_url": "http://localhost:11434/v1",
        "inference_engine": "llamacpp",
        "disable_reasoning": False,
    })
    body: dict = {}
    backend._apply_thinking_controls(body, enable_thinking=None)
    assert body["chat_template_kwargs"] == {"enable_thinking": True}


def test_llamacpp_cache_hints_set_cache_prompt():
    backend = HttpBackend({
        "api_url": "http://localhost:11434/v1",
        "inference_engine": "llamacpp",
    })
    body: dict = {}
    backend._apply_cache_hints(body)
    assert body["cache_prompt"] is True


def test_ollama_cache_hints_noop():
    backend = HttpBackend({
        "api_url": "http://localhost:11434/v1",
        "inference_engine": "ollama",
    })
    body: dict = {}
    backend._apply_cache_hints(body)
    assert "cache_prompt" not in body


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


def _fake_backend(message: dict) -> tuple[HttpBackend, dict]:
    """HttpBackend whose client records the request body and replies with `message`."""
    backend = HttpBackend({
        "api_url": "http://localhost:8000/v1",
        "inference_engine": "llamacpp",
    })
    captured: dict = {}

    class _FakeResponse:
        def raise_for_status(self): pass
        def json(self):
            return {
                "choices": [{"message": message, "finish_reason": "stop"}],
                "usage": {"total_tokens": 1},
            }

    class _FakeClient:
        async def post(self, url, json):
            captured["body"] = json
            return _FakeResponse()

    backend._client = _FakeClient()  # type: ignore[assignment]
    return backend, captured


@pytest.mark.asyncio
async def test_generate_passes_response_format_to_body():
    """response_format kwarg is forwarded to the OpenAI-compat request body."""
    backend, captured = _fake_backend({"content": "{}"})
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


@pytest.mark.asyncio
@pytest.mark.parametrize("with_tools", [False, True])
@pytest.mark.parametrize(
    ("message", "reasoning"),
    [
        ({"content": "the answer", "reasoning_content": "let me think"}, "let me think"),
        ({"content": "the answer", "reasoning": "ollama shape"}, "ollama shape"),
        ({"content": "plain", "reasoning_content": ""}, None),
        ({"content": "plain"}, None),
    ],
)
async def test_reasoning_content_surfaces_on_both_paths(message, reasoning, with_tools):
    backend, _ = _fake_backend(message)
    if with_tools:
        response = await backend.generate_with_tools(
            [{"role": "user", "content": "hi"}], [], max_tokens=10,
        )
    else:
        response = await backend.generate("hi", max_tokens=10)
    assert response.reasoning == reasoning
    assert response.text == message["content"]
