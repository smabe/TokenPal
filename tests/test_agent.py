"""Tests for the /agent multi-step tool-calling loop."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from tests._helpers import ScriptedLLM, assert_no_leak
from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.brain.agent import AgentRunner, AgentSession
from tokenpal.config.schema import AgentConfig
from tokenpal.llm.base import LLMResponse, ToolCall

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


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


def _list_log(logs: list[str] | None):
    """LogFn over a plain list, tagging unpersisted lines like capture_logs."""
    def _log(
        text: str, *, markup: bool = False, url: str | None = None, persist: bool = True,
    ) -> None:
        if logs is not None:
            logs.append(text if persist else f"{text} [unpersisted]")
    return _log


def _runner(
    llm: ScriptedLLM,
    actions: dict[str, AbstractAction] | None = None,
    *,
    confirm=_always_allow,
    is_sensitive=_no_sensitive,
    max_steps: int = 8,
    token_budget: int = 12000,
    per_step_timeout_s: float = 5.0,
    logs: list[str] | None = None,
    thinking: bool = False,
) -> AgentRunner:
    return AgentRunner(
        llm=llm,
        actions=actions if actions is not None else _echo_actions(),
        log_callback=_list_log(logs),
        confirm_callback=confirm,
        is_sensitive=is_sensitive,
        max_steps=max_steps,
        token_budget=token_budget,
        per_step_timeout_s=per_step_timeout_s,
        thinking=thinking,
    )


def _call(name: str, args: dict[str, Any] | None = None, call_id: str = "") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=args or {})


# ---------------------------------------------------------------------------
# Happy path + gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completes_when_model_emits_no_tool_call() -> None:
    llm = ScriptedLLM([
        LLMResponse(text="all done", tokens_used=42, model_name="t", latency_ms=0),
    ])
    session = await _runner(llm).run("greet me")

    assert session.stopped_reason == "complete"
    assert session.final_text == "all done"
    assert session.tokens_used == 42
    assert session.steps == []


@pytest.mark.asyncio
async def test_status_callback_reports_tool_name() -> None:
    """Each tool invoke should push a 'using <tool>...' label. The label
    persists through the follow-up LLM step so a fast gather isn't
    overwritten before the UI renders it."""
    llm = ScriptedLLM([
        LLMResponse(
            text="",
            tokens_used=10,
            model_name="t",
            latency_ms=0,
            tool_calls=[_call("echo", {"text": "hi"}, "call_1")],
        ),
        LLMResponse(text="echoed hi", tokens_used=20, model_name="t", latency_ms=0),
    ])
    statuses: list[str] = []
    runner = AgentRunner(
        llm=llm,
        actions=_echo_actions(),
        log_callback=_list_log(None),
        confirm_callback=_always_allow,
        is_sensitive=_no_sensitive,
        status_callback=statuses.append,
    )
    session = await runner.run("echo hi")

    assert session.stopped_reason == "complete"
    assert statuses == ["using echo..."]


@pytest.mark.asyncio
async def test_executes_tool_then_returns_final_text() -> None:
    llm = ScriptedLLM([
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
    llm = ScriptedLLM([
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
    llm = ScriptedLLM([
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
    llm = ScriptedLLM([
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
    llm = ScriptedLLM([
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

    llm = ScriptedLLM([
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
    llm = ScriptedLLM(looping + [forced])
    session = await _runner(llm, max_steps=3).run("loop forever")

    assert session.stopped_reason == "step_cap"
    assert len(session.steps) == 3  # 3 steps exhausted cap
    assert session.final_text == "ran out"


@pytest.mark.asyncio
async def test_forced_synthesis_uses_step_controls_and_counts_tokens() -> None:
    llm = ScriptedLLM([
        LLMResponse(
            text="", tokens_used=5, model_name="t", latency_ms=0,
            tool_calls=[_call("echo", {"text": "x"}, "c0")],
        ),
        LLMResponse(text="ran out", tokens_used=7, model_name="t", latency_ms=0),
    ])
    session = await _runner(llm, max_steps=1, thinking=True).run("loop")

    assert session.stopped_reason == "step_cap"
    assert session.final_text == "ran out"
    assert session.tokens_used == 12
    assert llm.calls[1][1] == []
    assert llm.call_kwargs[1] == {
        "max_tokens": 2048, "enable_thinking": True, "thinking_effort": "low",
    }


@pytest.mark.asyncio
async def test_token_budget_stops_loop() -> None:
    llm = ScriptedLLM([
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

    llm = ScriptedLLM([
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
    llm = ScriptedLLM([
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
    class _HangLLM(ScriptedLLM):
        async def generate_with_tools(
            self, messages, tools, max_tokens=None, **kwargs
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


# ---------------------------------------------------------------------------
# In-run result cache
# ---------------------------------------------------------------------------


class _Counting(AbstractAction):
    action_name = "counting"
    description = "Returns a running count."
    parameters = {"type": "object", "properties": {"x": {"type": "string"}}}
    safe = True
    requires_confirm = False
    calls = 0

    async def execute(self, **kwargs: Any) -> ActionResult:
        _Counting.calls += 1
        return ActionResult(output=f"n={_Counting.calls}")


@pytest.mark.asyncio
async def test_identical_tool_call_returns_cached_result() -> None:
    _Counting.calls = 0
    llm = ScriptedLLM([
        LLMResponse(
            text="",
            tokens_used=0,
            model_name="t",
            latency_ms=0,
            tool_calls=[_call("counting", {"x": "a"}, "call_1")],
        ),
        LLMResponse(
            text="",
            tokens_used=0,
            model_name="t",
            latency_ms=0,
            tool_calls=[_call("counting", {"x": "a"}, "call_2")],
        ),
        LLMResponse(text="done", tokens_used=0, model_name="t", latency_ms=0),
    ])
    logs: list[str] = []
    session = await _runner(
        llm, actions={"counting": _Counting({})}, logs=logs
    ).run("call twice")

    assert len(session.steps) == 2
    assert session.steps[0].cached is False
    assert session.steps[1].cached is True
    assert session.steps[0].result == "n=1"
    assert session.steps[1].result == "n=1"
    # Action body ran exactly once.
    assert _Counting.calls == 1
    assert any("(cached)" in line for line in logs)


@pytest.mark.asyncio
async def test_cache_key_is_order_insensitive() -> None:
    _Counting.calls = 0
    llm = ScriptedLLM([
        LLMResponse(
            text="",
            tokens_used=0,
            model_name="t",
            latency_ms=0,
            tool_calls=[_call("counting", {"x": "a", "y": "b"}, "c1")],
        ),
        LLMResponse(
            text="",
            tokens_used=0,
            model_name="t",
            latency_ms=0,
            tool_calls=[_call("counting", {"y": "b", "x": "a"}, "c2")],
        ),
        LLMResponse(text="done", tokens_used=0, model_name="t", latency_ms=0),
    ])
    session = await _runner(llm, actions={"counting": _Counting({})}).run("g")

    assert session.steps[1].cached is True
    assert _Counting.calls == 1


@pytest.mark.asyncio
async def test_noncacheable_tool_never_hits_cache() -> None:
    class _Live(_Counting):
        action_name = "live"
        cacheable = False

    _Counting.calls = 0
    llm = ScriptedLLM([
        LLMResponse(
            text="",
            tokens_used=0,
            model_name="t",
            latency_ms=0,
            tool_calls=[_call("live", {"x": "a"}, "c1")],
        ),
        LLMResponse(
            text="",
            tokens_used=0,
            model_name="t",
            latency_ms=0,
            tool_calls=[_call("live", {"x": "a"}, "c2")],
        ),
        LLMResponse(text="done", tokens_used=0, model_name="t", latency_ms=0),
    ])
    session = await _runner(llm, actions={"live": _Live({})}).run("g")
    assert [s.cached for s in session.steps] == [False, False]
    assert _Counting.calls == 2


# ---------------------------------------------------------------------------
# Thinking controls + truncation retry
# ---------------------------------------------------------------------------


def test_agent_config_defaults() -> None:
    cfg = AgentConfig()
    assert cfg.thinking is False
    assert cfg.thinking_effort == "low"
    assert cfg.per_step_timeout_s == 60.0
    assert cfg.max_tokens == 2048


@pytest.mark.asyncio
async def test_default_runner_sends_thinking_off_and_max_tokens() -> None:
    llm = ScriptedLLM([
        LLMResponse(text="done", tokens_used=5, model_name="t", latency_ms=0),
    ])
    await _runner(llm).run("go")

    assert llm.call_kwargs == [
        {"max_tokens": 2048, "enable_thinking": False, "thinking_effort": "low"},
    ]


@pytest.mark.asyncio
async def test_thinking_runner_sends_effort_and_logs_reasoning() -> None:
    reasoning = "First paragraph of thought.\n\nSecond paragraph, " + "x" * 600
    logs: list[str] = []
    llm = ScriptedLLM([
        LLMResponse(
            text="",
            tokens_used=50,
            model_name="t",
            latency_ms=0,
            tool_calls=[_call("echo", {"text": "hi"}, "c1")],
            reasoning=reasoning,
        ),
        LLMResponse(text="done", tokens_used=5, model_name="t", latency_ms=0),
    ])
    session = await _runner(llm, logs=logs, thinking=True).run("go")

    assert session.stopped_reason == "complete"
    assert llm.call_kwargs[0] == {
        "max_tokens": 2048, "enable_thinking": True, "thinking_effort": "low",
    }
    assert logs[0] == f"\u2026 {reasoning}"
    assert logs[1].startswith("\u2192 echo(")


@pytest.mark.asyncio
async def test_thinking_truncation_falls_back_to_no_thinking_for_the_run() -> None:
    logs: list[str] = []
    llm = ScriptedLLM([
        LLMResponse(
            text="",
            tokens_used=2048,
            model_name="t",
            latency_ms=0,
            finish_reason="length",
            reasoning="still thinking...",
        ),
        LLMResponse(
            text="", tokens_used=30, model_name="t", latency_ms=0,
            tool_calls=[_call("echo", {"text": "hi"}, "c1")],
        ),
        LLMResponse(
            text="answer", tokens_used=5, model_name="t", latency_ms=0,
            finish_reason="stop",
        ),
    ])
    session = await _runner(llm, logs=logs, thinking=True).run("go")

    assert [k["enable_thinking"] for k in llm.call_kwargs] == [True, False, False]
    assert session.stopped_reason == "complete"
    assert session.final_text == "answer"
    assert session.tokens_used == 2083
    assert logs[:2] == [
        "\u2026 still thinking...",
        "(step truncated while thinking; continuing without thinking)",
    ]


@pytest.mark.asyncio
async def test_truncated_answer_is_kept_not_retried() -> None:
    llm = ScriptedLLM([
        LLMResponse(
            text="a long answer that got cut", tokens_used=2048, model_name="t",
            latency_ms=0, finish_reason="length", reasoning="done thinking",
        ),
        LLMResponse(text="never", tokens_used=1, model_name="t", latency_ms=0),
    ])
    session = await _runner(llm, thinking=True).run("go")

    assert len(llm.call_kwargs) == 1
    assert session.final_text == "a long answer that got cut"


@pytest.mark.asyncio
async def test_truncation_without_thinking_does_not_retry() -> None:
    llm = ScriptedLLM([
        LLMResponse(
            text="cut off", tokens_used=2048, model_name="t", latency_ms=0,
            finish_reason="length",
        ),
        LLMResponse(text="never", tokens_used=1, model_name="t", latency_ms=0),
    ])
    session = await _runner(llm).run("go")

    assert len(llm.call_kwargs) == 1
    assert session.final_text == "cut off"
    assert session.stopped_reason == "complete"


# ---------------------------------------------------------------------------
# Desktop-content marker: trace redaction, no cache, tool drop
# ---------------------------------------------------------------------------

FIXTURE = "SECRET-FIXTURE-7731"


class _Reads(AbstractAction):
    action_name = "reads"
    description = "Reads text off the screen."
    parameters = {"type": "object", "properties": {}}
    safe = True
    requires_confirm = False
    reads_desktop_content = True
    calls = 0

    async def execute(self, **kwargs: Any) -> ActionResult:
        _Reads.calls += 1
        return ActionResult(output=FIXTURE)


class _Fact(AbstractAction):
    """Named after a real catalog entry so `_needs_consent` sees its
    `web_fetches` category."""

    action_name = "random_fact"
    description = "Fetches a fact."
    parameters = {"type": "object", "properties": {}}
    safe = True
    requires_confirm = False
    calls = 0

    async def execute(self, **kwargs: Any) -> ActionResult:
        _Fact.calls += 1
        return ActionResult(output="a fact")


def _marked_actions() -> dict[str, AbstractAction]:
    return {"reads": _Reads({}), "random_fact": _Fact({}), "echo": _Echo({})}


def _tool_call_response(*calls: ToolCall) -> LLMResponse:
    return LLMResponse(
        text="", tokens_used=0, model_name="t", latency_ms=0,
        tool_calls=list(calls),
    )


@pytest.mark.asyncio
async def test_marked_tool_result_is_redacted_in_trace(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    _Reads.calls = 0
    llm = ScriptedLLM([
        _tool_call_response(_call("reads", {}, "c1")),
        LLMResponse(text="all done", tokens_used=0, model_name="t", latency_ms=0),
    ])
    logs: list[str] = []
    session = await _runner(llm, actions=_marked_actions(), logs=logs).run("read it")

    assert f"← [desktop content: {len(FIXTURE)} chars, not shown] [unpersisted]" in logs
    assert session.desktop_content is True
    # The step still carries the text so the model can use it.
    assert session.steps[0].result == FIXTURE
    assert_no_leak(FIXTURE, lines=logs, caplog_text=caplog.text)


@pytest.mark.asyncio
async def test_reasoning_is_hidden_after_a_marked_tool(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    _Reads.calls = 0
    llm = ScriptedLLM([
        _tool_call_response(_call("reads", {}, "c1")),
        LLMResponse(
            text="all done", tokens_used=0, model_name="t", latency_ms=0,
            reasoning=f"mentions {FIXTURE}",
        ),
    ])
    logs: list[str] = []
    await _runner(llm, actions=_marked_actions(), logs=logs).run("read it")

    assert "… (reasoning hidden: desktop content in context) [unpersisted]" in logs
    assert_no_leak(FIXTURE, lines=logs, caplog_text=caplog.text)


@pytest.mark.asyncio
async def test_consent_gated_tools_are_dropped_after_a_marked_tool() -> None:
    _Reads.calls = 0
    llm = ScriptedLLM([
        _tool_call_response(_call("reads", {}, "c1")),
        LLMResponse(text="done", tokens_used=0, model_name="t", latency_ms=0),
    ])
    await _runner(llm, actions=_marked_actions()).run("read it")

    first_tools = [t["function"]["name"] for t in llm.calls[0][1]]
    second_tools = [t["function"]["name"] for t in llm.calls[1][1]]
    assert "random_fact" in first_tools
    assert "echo" in second_tools
    assert "random_fact" not in second_tools
    assert "reads" in second_tools


@pytest.mark.asyncio
async def test_marked_tool_is_never_cached() -> None:
    _Reads.calls = 0
    llm = ScriptedLLM([
        _tool_call_response(_call("reads", {}, "c1")),
        _tool_call_response(_call("reads", {}, "c2")),
        LLMResponse(text="done", tokens_used=0, model_name="t", latency_ms=0),
    ])
    session = await _runner(llm, actions=_marked_actions()).run("read twice")

    assert [s.cached for s in session.steps] == [False, False]
    assert _Reads.calls == 2


@pytest.mark.asyncio
async def test_gated_tool_in_the_same_batch_is_skipped() -> None:
    _Reads.calls = 0
    _Fact.calls = 0
    llm = ScriptedLLM([
        _tool_call_response(
            _call("reads", {}, "c1"), _call("random_fact", {}, "c2"),
        ),
        LLMResponse(text="done", tokens_used=0, model_name="t", latency_ms=0),
    ])
    logs: list[str] = []
    session = await _runner(llm, actions=_marked_actions(), logs=logs).run("both")

    assert session.steps[1].result == "skipped: desktop content is in context"
    assert _Fact.calls == 0
    assert "← skipped random_fact: desktop content is in context [unpersisted]" in logs


class _Raises(AbstractAction):
    """Marked tool that reads content, then throws with it in the message."""
    action_name = "raises"
    description = "raises"
    parameters = {"type": "object", "properties": {}}
    safe = True
    requires_confirm = False
    reads_desktop_content = True

    async def execute(self, **kwargs: Any) -> ActionResult:
        raise ValueError(f"could not parse screen text: {FIXTURE}")


@pytest.mark.asyncio
async def test_later_tool_arguments_are_never_persisted(caplog) -> None:
    """The model can copy screen text into a LATER tool's arguments. Those
    tools are local, so the consent-gated drop does not catch them; the trace
    must be unpersisted for the rest of the run instead."""
    caplog.set_level(logging.DEBUG)
    _Reads.calls = 0
    llm = ScriptedLLM([
        _tool_call_response(_call("reads", {}, "c1")),
        _tool_call_response(_call("echo", {"text": FIXTURE}, "c2")),
        LLMResponse(text="all done", tokens_used=0, model_name="t", latency_ms=0),
    ])
    logs: list[str] = []
    await _runner(llm, actions=_marked_actions(), logs=logs).run("read it")

    leaked = [ln for ln in logs if FIXTURE in ln and "[unpersisted]" not in ln]
    assert leaked == [], f"content reached a persisted trace line: {leaked}"
    assert any(FIXTURE in ln for ln in logs), "echo args should still be traced"
    assert_no_leak(FIXTURE, lines=logs, caplog_text=caplog.text)


@pytest.mark.asyncio
async def test_marked_tool_that_raises_redacts_and_still_sets_the_flag(caplog) -> None:
    """An exception can quote the text the tool just read. If that branch
    leaked, it would also leave the flag off and disable every other guard."""
    caplog.set_level(logging.DEBUG)
    llm = ScriptedLLM([
        _tool_call_response(_call("raises", {}, "c1")),
        LLMResponse(text="all done", tokens_used=0, model_name="t", latency_ms=0),
    ])
    logs: list[str] = []
    actions: dict[str, AbstractAction] = {"raises": _Raises({}), "echo": _Echo({})}
    session = await _runner(llm, actions=actions, logs=logs).run("read it")

    assert session.desktop_content is True
    assert_no_leak(FIXTURE, lines=logs, caplog_text=caplog.text)
    assert any("ValueError (details hidden)" in ln for ln in logs)
