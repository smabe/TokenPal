"""Tests for LLM backend tool-calling support."""

from __future__ import annotations

from typing import Any

from tokenpal.llm.base import AbstractLLMBackend, LLMResponse, ToolCall


class _MockBackend(AbstractLLMBackend):
    """Minimal backend that returns canned responses."""

    backend_name = "mock"
    platforms = ("darwin", "linux", "windows")

    def __init__(self, responses: list[LLMResponse]) -> None:
        super().__init__({})
        self._responses = list(responses)
        self._call_log: list[dict[str, Any]] = []

    async def setup(self) -> None:
        pass

    async def generate(
        self, prompt: str, max_tokens: int = 256, **_: Any
    ) -> LLMResponse:
        self._call_log.append({"method": "generate", "prompt": prompt})
        return self._responses.pop(0)

    async def teardown(self) -> None:
        pass


def test_tool_call_dataclass():
    tc = ToolCall(id="call_1", name="timer", arguments={"label": "test", "seconds": 5})
    assert tc.id == "call_1"
    assert tc.name == "timer"
    assert tc.arguments == {"label": "test", "seconds": 5}


def test_llm_response_defaults_empty_tool_calls():
    r = LLMResponse(text="hello", tokens_used=10, model_name="test", latency_ms=50.0)
    assert r.tool_calls == []


def test_llm_response_with_tool_calls():
    tc = ToolCall(id="call_1", name="timer", arguments={})
    r = LLMResponse(
        text="", tokens_used=10, model_name="test", latency_ms=50.0, tool_calls=[tc]
    )
    assert len(r.tool_calls) == 1
    assert r.tool_calls[0].name == "timer"


async def test_generate_with_tools_fallback():
    """Base class fallback extracts last user message and calls generate()."""
    canned = LLMResponse(text="hi", tokens_used=5, model_name="mock", latency_ms=10.0)
    backend = _MockBackend([canned])

    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "What time is it?"},
    ]
    tools = [{"type": "function", "function": {"name": "clock", "parameters": {}}}]

    result = await backend.generate_with_tools(messages, tools)
    assert result.text == "hi"
    assert backend._call_log[0]["prompt"] == "What time is it?"
