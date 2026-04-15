"""Tests for the /agent multi-step tool-calling loop."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.brain.agent import AgentRunner, AgentSession
from tokenpal.llm.base import AbstractLLMBackend, LLMResponse, ToolCall


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _ScriptedLLM(AbstractLLMBackend):
    """Returns pre-queued LLMResponse objects in order."""

    backend_name = "scripted"
    platforms = ("darwin", "linux", "windows")

    def __init__(self, responses: list[LLMResponse]) -> None:
        super().__init__({})
        self._responses = list(responses)
        self.calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    async def generate(self, prompt: str, max_tokens: int = 256) -> LLMResponse:
        return await self.generate_with_tools(
            [{"role": "user", "content": prompt}], [], max_tokens
        )

    async def generate_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 256,
    ) -> LLMResponse:
        self.calls.append((list(messages), list(tools)))
        if not self._responses:
            return LLMResponse(text="done", tokens_used=0, model_name="test", latency_ms=0)
        return self._responses.pop(0)


class _Echo(AbstractAction):
    action_name = "echo"
    description = "Echo the argument."
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    safe = True
    requires_confirm = False

    async def execute(self, **kwargs: Any) -> ActionResult:
        return ActionResult(output=f"echo:{kwargs.get('text', '')}")


class _Gated(AbstractAction):
    action_name = "gated"
    description = "Requires confirm."
    parameters = {"type": "object", "properties": {}}
    safe = False
    requires_confirm = True

    async def execute(self, **kwargs: Any) -> ActionResult:
        return ActionResult(output="gated-ran")


class _Slow(AbstractAction):
    action_name = "slow"
    description = "Blocks forever."
    parameters = {"type": "object", "properties": {}}
    safe = True
    requires_confirm = False

    async def execute(self, **kwargs: Any) -> ActionResult:
        await asyncio.sleep(10)
        return ActionResult(output="never")


class _Boom(AbstractAction):
    action_name = "boom"
    description = "Raises."
    parameters = {"type": "object", "properties": {}}
    safe = True
    requires_confirm = False

    async def execute(self, **kwargs: Any) -> ActionResult:
        raise RuntimeError("kaboom")


async def _always_allow(_name: str, _args: dict[str, Any]) -> bool:
    return True


async def _always_deny(_name: str, _args: dict[str, Any]) -> bool:
    return False


def _no_sensitive() -> bool:
    return False


def _echo_actions() -> dict[str, AbstractAction]:
    return {"echo": _Echo({})}


def _runner(
    llm: _ScriptedLLM,
    actions: dict[str, AbstractAction] | None = None,
    *,
    confirm=_always_allow,
    is_sensitive=_no_sensitive,
    max_steps: int = 8,
    token_budget: int = 12000,
    per_step_timeout_s: float = 5.0,
    logs: list[str] | None = None,
) -> AgentRunner:
    return AgentRunner(
        llm=llm,
        actions=actions if actions is not None else _echo_actions(),
        log_callback=(logs.append if logs is not None else (lambda _s: None)),
        confirm_callback=confirm,
        is_sensitive=is_sensitive,
        max_steps=max_steps,
        token_budget=token_budget,
        per_step_timeout_s=per_step_timeout_s,
    )


def _call(name: str, args: dict[str, Any] | None = None, call_id: str = "") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=args or {})


# ---------------------------------------------------------------------------
# Happy path + gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completes_when_model_emits_no_tool_call() -> None:
    llm = _ScriptedLLM([
        LLMResponse(text="all done", tokens_used=42, model_name="t", latency_ms=0),
    ])
    session = await _runner(llm).run("greet me")

    assert session.stopped_reason == "complete"
    assert session.final_text == "all done"
    assert session.tokens_used == 42
    assert session.steps == []


@pytest.mark.asyncio
async def test_executes_tool_then_returns_final_text() -> None:
    llm = _ScriptedLLM([
        LLMResponse(
            text="",
            tokens_used=10,
            model_name="t",
            latency_ms=0,
            tool_calls=[_call("echo", {"text": "hi"}, "call_1")],
        ),
        LLMResponse(text="echoed hi", tokens_used=20, model_name="t", latency_ms=0),
    ])
    logs: list[str] = []
    session = await _runner(llm, logs=logs).run("echo hi")

    assert session.stopped_reason == "complete"
    assert session.final_text == "echoed hi"
    assert session.tokens_used == 30
    assert len(session.steps) == 1
    step = session.steps[0]
    assert step.tool_name == "echo"
    assert step.result == "echo:hi"
    assert step.denied is False
    assert any("\u2192 echo" in line for line in logs)
    assert any("\u2190 echo:hi" in line for line in logs)


@pytest.mark.asyncio
async def test_empty_tool_call_id_gets_fallback() -> None:
    """Ollama sometimes emits empty id strings — the agent must substitute."""
    llm = _ScriptedLLM([
        LLMResponse(
            text="",
            tokens_used=0,
            model_name="t",
            latency_ms=0,
            tool_calls=[_call("echo", {"text": "x"})],  # no id
        ),
        LLMResponse(text="ok", tokens_used=0, model_name="t", latency_ms=0),
    ])
    session = await _runner(llm).run("go")

    # The tool message that went into the LLM must carry the synthesized id.
    second_call_messages = llm.calls[1][0]
    tool_msg = [m for m in second_call_messages if m.get("role") == "tool"][0]
    assert tool_msg["tool_call_id"] == "call_0"
    assert session.stopped_reason == "complete"


@pytest.mark.asyncio
async def test_unknown_tool_records_error_not_crash() -> None:
    llm = _ScriptedLLM([
        LLMResponse(
            text="",
            tokens_used=0,
            model_name="t",
            latency_ms=0,
            tool_calls=[_call("nope", {}, "call_1")],
        ),
        LLMResponse(text="gave up", tokens_used=0, model_name="t", latency_ms=0),
    ])
    session = await _runner(llm).run("call nope")

    assert session.steps[0].result == "Unknown tool 'nope'."
    assert session.stopped_reason == "complete"


@pytest.mark.asyncio
async def test_tool_exception_captured_as_step_result() -> None:
    llm = _ScriptedLLM([
        LLMResponse(
            text="",
            tokens_used=0,
            model_name="t",
            latency_ms=0,
            tool_calls=[_call("boom", {}, "c1")],
        ),
        LLMResponse(text="crashed tool", tokens_used=0, model_name="t", latency_ms=0),
    ])
    session = await _runner(llm, actions={"boom": _Boom({})}).run("trigger boom")

    assert "kaboom" in session.steps[0].result
    assert session.stopped_reason == "complete"


# ---------------------------------------------------------------------------
# Confirm gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_denied_tool_aborts_with_forced_synthesis() -> None:
    llm = _ScriptedLLM([
        LLMResponse(
            text="",
            tokens_used=0,
            model_name="t",
            latency_ms=0,
            tool_calls=[_call("gated", {}, "c1")],
        ),
        LLMResponse(text="ok, I won't.", tokens_used=0, model_name="t", latency_ms=0),
    ])
    session = await _runner(
        llm, actions={"gated": _Gated({})}, confirm=_always_deny
    ).run("do the risky thing")

    assert session.stopped_reason == "denied"
    assert session.steps[0].denied is True
    assert session.final_text == "ok, I won't."


@pytest.mark.asyncio
async def test_confirmed_tool_executes() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def recording_confirm(name: str, args: dict[str, Any]) -> bool:
        calls.append((name, args))
        return True

    llm = _ScriptedLLM([
        LLMResponse(
            text="",
            tokens_used=0,
            model_name="t",
            latency_ms=0,
            tool_calls=[_call("gated", {}, "c1")],
        ),
        LLMResponse(text="done", tokens_used=0, model_name="t", latency_ms=0),
    ])
    session = await _runner(
        llm, actions={"gated": _Gated({})}, confirm=recording_confirm
    ).run("go")

    assert calls == [("gated", {})]
    assert session.steps[0].result == "gated-ran"
    assert session.steps[0].denied is False
    assert session.stopped_reason == "complete"


# ---------------------------------------------------------------------------
# Caps + timeouts + sensitive kill
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_step_cap_stops_loop_and_forces_synthesis() -> None:
    # Every response emits one tool call, never a final — should hit cap.
    looping = [
        LLMResponse(
            text="",
            tokens_used=5,
            model_name="t",
            latency_ms=0,
            tool_calls=[_call("echo", {"text": "x"}, f"c{i}")],
        )
        for i in range(3)
    ]
    forced = LLMResponse(text="ran out", tokens_used=0, model_name="t", latency_ms=0)
    llm = _ScriptedLLM(looping + [forced])
    session = await _runner(llm, max_steps=3).run("loop forever")

    assert session.stopped_reason == "step_cap"
    assert len(session.steps) == 3  # 3 steps exhausted cap
    assert session.final_text == "ran out"


@pytest.mark.asyncio
async def test_token_budget_stops_loop() -> None:
    llm = _ScriptedLLM([
        LLMResponse(
            text="",
            tokens_used=9999,
            model_name="t",
            latency_ms=0,
            tool_calls=[_call("echo", {"text": "x"}, "c1")],
        ),
        LLMResponse(text="over budget", tokens_used=0, model_name="t", latency_ms=0),
    ])
    session = await _runner(llm, token_budget=500).run("heavy")

    assert session.stopped_reason == "token_budget"
    assert session.final_text == "over budget"


@pytest.mark.asyncio
async def test_sensitive_app_detected_mid_run_aborts() -> None:
    trigger = {"fired": False}

    def is_sensitive() -> bool:
        if trigger["fired"]:
            return True
        trigger["fired"] = True  # sensitive on the SECOND check
        return False

    llm = _ScriptedLLM([
        LLMResponse(
            text="",
            tokens_used=5,
            model_name="t",
            latency_ms=0,
            tool_calls=[_call("echo", {"text": "x"}, "c1")],
        ),
        LLMResponse(
            text="",
            tokens_used=5,
            model_name="t",
            latency_ms=0,
            tool_calls=[_call("echo", {"text": "y"}, "c2")],
        ),
    ])
    session = await _runner(llm, is_sensitive=is_sensitive).run("keep going")

    assert session.stopped_reason == "sensitive"


@pytest.mark.asyncio
async def test_tool_timeout_does_not_crash_loop() -> None:
    llm = _ScriptedLLM([
        LLMResponse(
            text="",
            tokens_used=0,
            model_name="t",
            latency_ms=0,
            tool_calls=[_call("slow", {}, "c1")],
        ),
        LLMResponse(text="moved on", tokens_used=0, model_name="t", latency_ms=0),
    ])
    session = await _runner(
        llm, actions={"slow": _Slow({})}, per_step_timeout_s=0.05
    ).run("patience")

    assert "timed out" in session.steps[0].result
    assert session.stopped_reason == "complete"


@pytest.mark.asyncio
async def test_llm_step_timeout_stops_run() -> None:
    class _HangLLM(_ScriptedLLM):
        async def generate_with_tools(
            self, messages, tools, max_tokens=256
        ):
            await asyncio.sleep(5)
            raise AssertionError("should have timed out")

    session = await _runner(_HangLLM([]), per_step_timeout_s=0.05).run("hang")

    assert session.stopped_reason == "timeout"


# ---------------------------------------------------------------------------
# Summary formatter + session helpers
# ---------------------------------------------------------------------------


def test_agent_session_is_complete_helper() -> None:
    assert AgentSession(goal="x", stopped_reason="complete").is_complete is True
    assert AgentSession(goal="x", stopped_reason="step_cap").is_complete is False


def test_summary_formatter_reports_each_reason() -> None:
    from tokenpal.brain.orchestrator import _format_agent_summary

    for reason in ("complete", "step_cap", "token_budget", "sensitive", "denied", "timeout"):
        session = AgentSession(goal="g", stopped_reason=reason)
        summary = _format_agent_summary(session)
        assert "step(s)" in summary
        assert "tokens" in summary
