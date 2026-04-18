"""Tests for the Brain's tool-calling loop."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.brain.orchestrator import Brain
from tokenpal.brain.personality import PersonalityEngine
from tokenpal.llm.base import AbstractLLMBackend, LLMResponse, ToolCall


class _StubAction(AbstractAction):
    action_name = "stub"
    description = "Returns a fixed string."
    parameters = {"type": "object", "properties": {}}

    def __init__(self) -> None:
        super().__init__({})
        self.call_count = 0

    async def execute(self, **kwargs: Any) -> ActionResult:
        self.call_count += 1
        return ActionResult(output="stub result")


class _FailAction(AbstractAction):
    action_name = "fail"
    description = "Always fails."
    parameters = {"type": "object", "properties": {}}

    def __init__(self) -> None:
        super().__init__({})

    async def execute(self, **kwargs: Any) -> ActionResult:
        raise RuntimeError("boom")


class _MockLLM(AbstractLLMBackend):
    backend_name = "mock"
    platforms = ("darwin", "linux", "windows")

    def __init__(self, responses: list[LLMResponse]) -> None:
        super().__init__({"max_tokens": 40})
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def setup(self) -> None:
        pass

    async def generate(
        self, prompt: str, max_tokens: int = 256, **_: Any
    ) -> LLMResponse:
        return self._responses.pop(0)

    async def generate_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 256,
        **kwargs: Any,
    ) -> LLMResponse:
        self.calls.append({"messages": messages, "tools": tools, **kwargs})
        return self._responses.pop(0)

    async def teardown(self) -> None:
        pass


def _make_brain(
    llm: _MockLLM,
    actions: list[AbstractAction] | None = None,
) -> Brain:
    personality = PersonalityEngine(
        "You are a test bot. Say 'ok' or [SILENT]."
    )
    return Brain(
        senses=[],
        llm=llm,
        ui_callback=MagicMock(),
        personality=personality,
        actions=actions,
    )


async def test_no_tool_calls_returns_text():
    """When LLM returns text without tool_calls, return immediately."""
    response = LLMResponse(
        text="Just a comment.", tokens_used=10, model_name="mock", latency_ms=5.0
    )
    llm = _MockLLM([response])
    brain = _make_brain(llm, actions=[_StubAction()])

    result = await brain._generate_with_tools("test prompt")
    assert result.text == "Just a comment."
    assert llm.calls[0]["tools"]  # tools were sent


async def test_single_tool_call_round():
    """LLM calls a tool, gets result, then responds with text."""
    tool_response = LLMResponse(
        text="",
        tokens_used=10,
        model_name="mock",
        latency_ms=5.0,
        tool_calls=[ToolCall(id="call_1", name="stub", arguments={})],
    )
    final_response = LLMResponse(
        text="Here's the result.", tokens_used=15, model_name="mock", latency_ms=5.0
    )
    llm = _MockLLM([tool_response, final_response])
    stub = _StubAction()
    brain = _make_brain(llm, actions=[stub])

    result = await brain._generate_with_tools("test")
    assert result.text == "Here's the result."
    assert stub.call_count == 1

    # Second call should have tool result in messages
    second_call_msgs = llm.calls[1]["messages"]
    tool_msg = [m for m in second_call_msgs if m["role"] == "tool"]
    assert len(tool_msg) == 1
    assert tool_msg[0]["content"] == "stub result"
    assert tool_msg[0]["tool_call_id"] == "call_1"


async def test_unknown_tool_handled():
    """LLM calls a tool that doesn't exist — gets error message, continues."""
    tool_response = LLMResponse(
        text="",
        tokens_used=10,
        model_name="mock",
        latency_ms=5.0,
        tool_calls=[ToolCall(id="call_1", name="nonexistent", arguments={})],
    )
    final_response = LLMResponse(
        text="Oops.", tokens_used=10, model_name="mock", latency_ms=5.0
    )
    llm = _MockLLM([tool_response, final_response])
    brain = _make_brain(llm, actions=[_StubAction()])

    result = await brain._generate_with_tools("test")
    assert result.text == "Oops."

    tool_msg = [m for m in llm.calls[1]["messages"] if m["role"] == "tool"]
    assert "Unknown tool" in tool_msg[0]["content"]


async def test_tool_execution_error_handled():
    """Tool raises an exception — error message fed back to LLM."""
    tool_response = LLMResponse(
        text="",
        tokens_used=10,
        model_name="mock",
        latency_ms=5.0,
        tool_calls=[ToolCall(id="call_1", name="fail", arguments={})],
    )
    final_response = LLMResponse(
        text="Something broke.", tokens_used=10, model_name="mock", latency_ms=5.0
    )
    llm = _MockLLM([tool_response, final_response])
    brain = _make_brain(llm, actions=[_FailAction()])

    result = await brain._generate_with_tools("test")
    assert result.text == "Something broke."

    tool_msg = [m for m in llm.calls[1]["messages"] if m["role"] == "tool"]
    assert "Error: boom" in tool_msg[0]["content"]


async def test_max_rounds_forces_text():
    """After _MAX_TOOL_ROUNDS, sends final call without tools."""
    # Every response requests a tool call
    tool_responses = [
        LLMResponse(
            text="",
            tokens_used=10,
            model_name="mock",
            latency_ms=5.0,
            tool_calls=[ToolCall(id=f"call_{i}", name="stub", arguments={})],
        )
        for i in range(Brain._MAX_TOOL_ROUNDS)
    ]
    final = LLMResponse(
        text="Gave up.", tokens_used=10, model_name="mock", latency_ms=5.0
    )
    llm = _MockLLM(tool_responses + [final])
    brain = _make_brain(llm, actions=[_StubAction()])

    result = await brain._generate_with_tools("test")
    assert result.text == "Gave up."

    # Final call should have empty tools list
    assert llm.calls[-1]["tools"] == []


async def test_multiple_tool_calls_parallel():
    """Multiple tool calls in one response are executed (via gather)."""
    tool_response = LLMResponse(
        text="",
        tokens_used=10,
        model_name="mock",
        latency_ms=5.0,
        tool_calls=[
            ToolCall(id="call_1", name="stub", arguments={}),
            ToolCall(id="call_2", name="stub", arguments={}),
        ],
    )
    final = LLMResponse(
        text="Both done.", tokens_used=10, model_name="mock", latency_ms=5.0
    )
    llm = _MockLLM([tool_response, final])
    stub = _StubAction()
    brain = _make_brain(llm, actions=[stub])

    result = await brain._generate_with_tools("test")
    assert result.text == "Both done."
    assert stub.call_count == 2

    tool_msgs = [m for m in llm.calls[1]["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 2


async def test_deadline_propagates_remaining_wallclock():
    """Each tool round receives target_latency_s = deadline - now, not a
    static target/N divide. Round 2 should see a smaller budget than round 1."""
    tool_response_1 = LLMResponse(
        text="",
        tokens_used=10,
        model_name="mock",
        latency_ms=5.0,
        tool_calls=[ToolCall(id="c1", name="stub", arguments={})],
    )
    tool_response_2 = LLMResponse(
        text="",
        tokens_used=10,
        model_name="mock",
        latency_ms=5.0,
        tool_calls=[ToolCall(id="c2", name="stub", arguments={})],
    )
    final = LLMResponse(text="Done.", tokens_used=5, model_name="mock", latency_ms=5.0)
    llm = _MockLLM([tool_response_1, tool_response_2, final])
    brain = _make_brain(llm, actions=[_StubAction()])

    await brain._generate_with_tools(
        "test", target_latency_s=8.0, min_tokens=60,
    )
    # Three calls: two tool rounds + final text. Each saw a budget.
    budgets = [c.get("target_latency_s") for c in llm.calls]
    assert all(b is not None for b in budgets)
    # Budgets are monotonically non-increasing — deadline ticks forward.
    assert budgets[0] >= budgets[1] >= budgets[2]
    # First round sees ~full budget (work is async-fast here so ≈8.0).
    assert budgets[0] <= 8.0
    # min_tokens passed unchanged.
    assert all(c.get("min_tokens") == 60 for c in llm.calls)


async def test_no_target_latency_means_no_kwarg_forwarded():
    """Legacy callers that don't pass target_latency_s get None forwarded,
    so the backend stays in static-default mode."""
    response = LLMResponse(text="ok", tokens_used=5, model_name="mock", latency_ms=1.0)
    llm = _MockLLM([response])
    brain = _make_brain(llm, actions=[_StubAction()])
    await brain._generate_with_tools("test")
    assert llm.calls[0].get("target_latency_s") is None


async def test_assistant_message_has_tool_calls_json():
    """The assistant message sent back includes properly serialized tool_calls."""
    tool_response = LLMResponse(
        text="thinking...",
        tokens_used=10,
        model_name="mock",
        latency_ms=5.0,
        tool_calls=[ToolCall(id="call_1", name="stub", arguments={"key": "val"})],
    )
    final = LLMResponse(
        text="Done.", tokens_used=10, model_name="mock", latency_ms=5.0
    )
    llm = _MockLLM([tool_response, final])
    brain = _make_brain(llm, actions=[_StubAction()])

    await brain._generate_with_tools("test")

    assistant_msg = [m for m in llm.calls[1]["messages"] if m["role"] == "assistant"][0]
    assert assistant_msg["content"] == "thinking..."
    tc = assistant_msg["tool_calls"][0]
    assert tc["id"] == "call_1"
    assert tc["function"]["name"] == "stub"
    assert json.loads(tc["function"]["arguments"]) == {"key": "val"}
