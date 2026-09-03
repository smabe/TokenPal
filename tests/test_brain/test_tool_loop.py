"""Tests for the Brain's tool-calling loop."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from tests._helpers import assert_no_leak, capture_logs
from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.brain.agent import AgentRunner
from tokenpal.brain.memory import MemoryStore
from tokenpal.brain.orchestrator import AgentBridge, Brain
from tokenpal.brain.personality import PersonalityEngine
from tokenpal.config.schema import AgentConfig
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
        self.prompts: list[str] = []

    async def setup(self) -> None:
        pass

    async def generate(
        self, prompt: str, max_tokens: int = 256, **_: Any
    ) -> LLMResponse:
        self.prompts.append(prompt)
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
    status_callback: Any = None,
    agent_bridge: AgentBridge | None = None,
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
        status_callback=status_callback,
        agent_bridge=agent_bridge,
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


async def test_tool_call_surfaces_to_status_bar():
    """During tool execution the status bar should say 'using <tool>...'
    and stay visible through the follow-up LLM round — a fast gather
    would otherwise be overwritten before the UI can render it."""
    tool_response = LLMResponse(
        text="",
        tokens_used=10,
        model_name="mock",
        latency_ms=5.0,
        tool_calls=[
            ToolCall(id="c1", name="stub", arguments={}),
            ToolCall(id="c2", name="stub", arguments={}),
        ],
    )
    final = LLMResponse(text="done", tokens_used=5, model_name="mock", latency_ms=1.0)
    llm = _MockLLM([tool_response, final])
    statuses: list[str] = []
    brain = _make_brain(
        llm, actions=[_StubAction()], status_callback=statuses.append,
    )

    await brain._generate_with_tools("test")

    # Tool label was set and nothing overwrote it inside the loop.
    assert statuses == ["using stub, stub..."]


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


# ---------------------------------------------------------------------------
# Desktop-content gating: hidden from chat, agent-only, delivered unpersisted
# ---------------------------------------------------------------------------

FIXTURE = "SECRET-FIXTURE-7731"
GOAL = "read the thing on screen"
PERSONA_LINE = "Peeked and stashed it in the log for you."


class _ReadsAction(AbstractAction):
    action_name = "reads"
    description = "Reads text off the screen."
    parameters = {"type": "object", "properties": {}}
    safe = True
    requires_confirm = False
    reads_desktop_content = True

    def __init__(self) -> None:
        super().__init__({})
        self.call_count = 0

    async def execute(self, **kwargs: Any) -> ActionResult:
        self.call_count += 1
        return ActionResult(output=FIXTURE)


class _EchoAction(AbstractAction):
    action_name = "echo"
    description = "Echoes."
    parameters = {"type": "object", "properties": {}}
    safe = True
    requires_confirm = False

    def __init__(self) -> None:
        super().__init__({})

    async def execute(self, **kwargs: Any) -> ActionResult:
        return ActionResult(output="echoed")


async def _allow(_name: str, _args: dict[str, Any]) -> bool:
    return True


def _reads_call() -> LLMResponse:
    return LLMResponse(
        text="",
        tokens_used=1,
        model_name="mock",
        latency_ms=0.0,
        tool_calls=[ToolCall(id="c1", name="reads", arguments={})],
    )


async def test_conversation_specs_exclude_desktop_content_tools() -> None:
    llm = _MockLLM([])
    brain = _make_brain(llm, actions=[_ReadsAction(), _EchoAction()])

    names = [spec["function"]["name"] for spec in brain._tool_specs]
    assert names == ["echo"]


async def test_conversation_executor_refuses_a_desktop_content_tool() -> None:
    final = LLMResponse(
        text="ok then", tokens_used=5, model_name="mock", latency_ms=0.0
    )
    llm = _MockLLM([_reads_call(), final])
    reads = _ReadsAction()
    brain = _make_brain(llm, actions=[reads, _EchoAction()])

    await brain._generate_with_tools("test")

    assert reads.call_count == 0
    tool_msgs = [m for m in llm.calls[1]["messages"] if m["role"] == "tool"]
    assert tool_msgs[0]["content"] == "Tool 'reads' is only available in /agent."


def _agent_brain(
    llm: _MockLLM, reads: _ReadsAction, memory: MemoryStore,
) -> tuple[Brain, list[str]]:
    buf, capture = capture_logs()

    def _log(text: str, *, markup: bool = False, url: str | None = None,
             persist: bool = True) -> None:
        capture(text, markup=markup, url=url, persist=persist)
        if persist:
            memory.record_chat_entry(speaker="buddy", text=text, url=url)

    bridge = AgentBridge(
        config=AgentConfig(),
        log_callback=_log,
        confirm_callback=_allow,
    )
    brain = _make_brain(
        llm, actions=[reads, _EchoAction()], agent_bridge=bridge,
    )
    return brain, buf


async def test_agent_run_with_desktop_content_delivers_unpersisted(
    tmp_path: Path, caplog: Any,
) -> None:
    caplog.set_level(logging.DEBUG)
    memory = MemoryStore(tmp_path / "m.db")
    memory.setup()
    memory.set_chat_log_max_persisted(50)
    try:
        llm = _MockLLM([
            _reads_call(),
            LLMResponse(
                text=f"The screen says {FIXTURE}.",
                tokens_used=5, model_name="mock", latency_ms=0.0,
            ),
            LLMResponse(
                text=PERSONA_LINE, tokens_used=5, model_name="mock", latency_ms=0.0,
            ),
        ])
        reads = _ReadsAction()
        brain, buf = _agent_brain(llm, reads, memory)

        session = await brain._handle_agent_goal(GOAL)

        assert session.desktop_content is True
        assert reads.call_count == 1
        # The agent saw both tools; the conversation path would not have.
        agent_tools = [t["function"]["name"] for t in llm.calls[0]["tools"]]
        assert sorted(agent_tools) == ["echo", "reads"]
        # Trace redacted, final answer delivered but never persisted.
        assert f"← [desktop content: {len(FIXTURE)} chars, not shown] [unpersisted]" in buf
        assert f"The screen says {FIXTURE}. [unpersisted]" in buf
        # Bubble is the persona line, from a prompt carrying neither the
        # fixture nor the goal.
        assert brain._ui_callback.call_args[0][0] == PERSONA_LINE
        assert FIXTURE not in llm.prompts[-1]
        assert GOAL not in llm.prompts[-1]

        # The pane shows the answer; every line that reaches a sink is clean.
        assert_no_leak(
            FIXTURE, lines=buf, caplog_text=caplog.text, memory=memory,
        )
    finally:
        memory.teardown()


async def test_desktop_done_line_falls_back_when_persona_is_filtered(
    tmp_path: Path,
) -> None:
    memory = MemoryStore(tmp_path / "m.db")
    memory.setup()
    memory.set_chat_log_max_persisted(50)
    try:
        llm = _MockLLM([
            _reads_call(),
            LLMResponse(
                text=f"The screen says {FIXTURE}.",
                tokens_used=5, model_name="mock", latency_ms=0.0,
            ),
            LLMResponse(text="", tokens_used=0, model_name="mock", latency_ms=0.0),
        ])
        brain, _buf = _agent_brain(llm, _ReadsAction(), memory)

        await brain._handle_agent_goal(GOAL)

        assert brain._ui_callback.call_args[0][0] == (
            "Done. The answer is in the chat log and was not saved."
        )
    finally:
        memory.teardown()


async def test_desktop_done_line_falls_back_when_persona_text_is_rejected(
    tmp_path: Path,
) -> None:
    """The empty-response case only proves ``"" or FALLBACK``. This pins the
    real guard: a non-empty line that ``filter_response`` rejects must not
    reach the bubble. Without it, dropping filter_response breaks no test."""
    memory = MemoryStore(tmp_path / "m.db")
    memory.setup()
    memory.set_chat_log_max_persisted(50)
    try:
        llm = _MockLLM([
            _reads_call(),
            LLMResponse(
                text=f"The screen says {FIXTURE}.",
                tokens_used=5, model_name="mock", latency_ms=0.0,
            ),
            # Non-empty, but filter_response drops replies under 15 chars.
            LLMResponse(text="ok", tokens_used=1, model_name="mock", latency_ms=0.0),
        ])
        brain, _buf = _agent_brain(llm, _ReadsAction(), memory)

        await brain._handle_agent_goal(GOAL)

        bubble = brain._ui_callback.call_args[0][0]
        assert bubble == "Done. The answer is in the chat log and was not saved."
        assert "ok" != bubble
    finally:
        memory.teardown()


async def test_aborted_desktop_run_does_not_promise_an_answer(
    tmp_path: Path,
) -> None:
    """A run that reads content then ends with no answer must not show the
    "it's in the chat log" line — there is nothing there to find."""
    memory = MemoryStore(tmp_path / "m.db")
    memory.setup()
    memory.set_chat_log_max_persisted(50)
    try:
        llm = _MockLLM([
            _reads_call(),
            LLMResponse(text="", tokens_used=0, model_name="mock", latency_ms=0.0),
        ])
        brain, _buf = _agent_brain(llm, _ReadsAction(), memory)

        session = await brain._handle_agent_goal(GOAL)

        assert session.desktop_content is True
        assert session.final_text.strip() == ""
        bubble = brain._ui_callback.call_args[0][0]
        assert bubble == "Nothing came back that time — and nothing was saved."
    finally:
        memory.teardown()


async def test_reenabling_tool_calling_keeps_desktop_tools_out_of_conversation() -> None:
    """Drives the real recovery branch in _generate_comment, not the builder.

    The circuit breaker empties the spec list after three failures; the
    recovery path used to rebuild it unfiltered, re-exposing the marked tool
    to the conversation model for the rest of the session.
    """
    llm = _MockLLM([])
    brain = _make_brain(llm, actions=[_ReadsAction(), _EchoAction()])
    assert [s["function"]["name"] for s in brain._tool_specs] == ["echo"]

    # Trip the breaker exactly as three failed generations would.
    brain._consecutive_failures = 3
    brain._tool_calling_enabled = False
    brain._tool_specs = []

    # A successful generation runs the recovery branch.
    llm._responses = [
        LLMResponse(
            text="something long enough to survive the response filter",
            tokens_used=5, model_name="mock", latency_ms=0.0,
        ),
    ]
    await brain._generate_comment("snapshot")

    assert brain._tool_calling_enabled is True
    assert [s["function"]["name"] for s in brain._tool_specs] == ["echo"]


async def test_crash_after_a_desktop_read_keeps_the_marker(tmp_path: Path) -> None:
    """A crash discards the session object. If the marker went with it, the
    answer would be delivered through the persisted branch."""
    memory = MemoryStore(tmp_path / "m.db")
    memory.setup()
    memory.set_chat_log_max_persisted(50)
    try:
        llm = _MockLLM([_reads_call()])
        brain, _buf = _agent_brain(llm, _ReadsAction(), memory)

        original = AgentRunner.run

        async def _run(self, goal: str):
            await original(self, goal)
            raise RuntimeError("backend died mid-run")

        AgentRunner.run = _run  # type: ignore[method-assign]
        try:
            session = await brain._handle_agent_goal(GOAL)
        finally:
            AgentRunner.run = original  # type: ignore[method-assign]

        assert session.stopped_reason == "crashed"
        assert session.desktop_content is True
    finally:
        memory.teardown()


def test_agent_still_gets_tools_when_every_action_is_desktop_content() -> None:
    """The agent's tool list must not key off the conversation-filtered list:
    a user who enables only the screen reader would get an agent with none."""
    llm = _MockLLM([])
    brain = _make_brain(llm, actions=[_ReadsAction()])

    assert brain._tool_specs == []
    assert [s["function"]["name"] for s in brain._build_agent_specs()] == ["reads"]

    brain._tool_calling_enabled = False
    assert brain._build_agent_specs() == []
