"""Multi-step agent loop for /agent <goal>.

Separate from the observation/conversation paths because the constraints are
different: bigger step cap, token budget, per-step timeout, confirm gate for
side-effectful tools, sensitive-app kill switch, and a live trace streamed to
the chat log so the user can follow along.

The loop is thin: LLM call with tools → execute tool calls → feed results
back → repeat until no tool calls, a cap trips, or the user denies a confirm.
State stays in memory; no checkpointer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from tokenpal.actions.base import AbstractAction
from tokenpal.llm.base import AbstractLLMBackend, ToolCall

log = logging.getLogger(__name__)

# Cap for each tool result *as fed back to the LLM*. Without this, a single
# verbose tool (system_info, list_processes) blows the prompt-context window
# well before the cumulative completion-token budget trips.
_MESSAGE_RESULT_CAP = 2048
# Cap for each tool result *kept in memory on AgentSession*. Wider so the
# user can still read the full output in the trace, tighter than unbounded.
_SESSION_RESULT_CAP = 4096


ConfirmFn = Callable[[str, dict[str, Any]], Awaitable[bool]]
SensitiveFn = Callable[[], bool]
LogFn = Callable[[str], None]


class StopReason(StrEnum):
    COMPLETE = "complete"
    STEP_CAP = "step_cap"
    TOKEN_BUDGET = "token_budget"
    SENSITIVE = "sensitive"
    DENIED = "denied"
    TIMEOUT = "timeout"
    CRASHED = "crashed"
    UNAVAILABLE = "unavailable"


@dataclass
class AgentStep:
    """One executed tool call or final-text step in an agent run."""

    tool_name: str
    arguments: dict[str, Any]
    result: str
    duration_ms: float
    denied: bool = False


@dataclass
class AgentSession:
    """Result of a single /agent run."""

    goal: str
    steps: list[AgentStep] = field(default_factory=list)
    final_text: str = ""
    tokens_used: int = 0
    stopped_reason: StopReason | str = ""
    started_at: float = field(default_factory=time.monotonic)

    @property
    def is_complete(self) -> bool:
        return self.stopped_reason == StopReason.COMPLETE


class AgentRunner:
    """Runs a single agent session end-to-end.

    The runner does NOT manage model swapping, observation suppression, or
    UI bubble display — those are the caller's concern (see Brain.run_agent).
    This class is deliberately framework-agnostic so it can be unit-tested
    with a mock LLM and a dict of actions.
    """

    def __init__(
        self,
        llm: AbstractLLMBackend,
        actions: dict[str, AbstractAction],
        *,
        log_callback: LogFn,
        confirm_callback: ConfirmFn,
        is_sensitive: SensitiveFn,
        tool_specs: list[dict[str, Any]] | None = None,
        max_steps: int = 8,
        token_budget: int = 12000,
        per_step_timeout_s: float = 45.0,
        system_prompt: str | None = None,
    ) -> None:
        self._llm = llm
        self._actions = actions
        self._log = log_callback
        self._confirm = confirm_callback
        self._is_sensitive = is_sensitive
        self._tool_specs = (
            tool_specs
            if tool_specs is not None
            else [a.to_tool_spec() for a in actions.values()]
        )
        self._max_steps = max_steps
        self._token_budget = token_budget
        self._per_step_timeout_s = per_step_timeout_s
        self._system_prompt = system_prompt or _DEFAULT_SYSTEM_PROMPT

    async def run(self, goal: str) -> AgentSession:
        session = AgentSession(goal=goal)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": goal},
        ]

        for step in range(self._max_steps):
            if self._is_sensitive():
                session.stopped_reason = StopReason.SENSITIVE
                log.info("Agent aborted mid-run: sensitive app detected")
                return session

            # Ollama sometimes returns tokens_used=0, so this is a soft cap —
            # if usage data is bad, step_cap still bounds the run.
            if session.tokens_used >= self._token_budget:
                session.stopped_reason = StopReason.TOKEN_BUDGET
                log.info(
                    "Agent hit token budget (%d/%d)",
                    session.tokens_used,
                    self._token_budget,
                )
                session.final_text = await self._force_synthesis(messages)
                return session

            try:
                response = await asyncio.wait_for(
                    self._llm.generate_with_tools(
                        messages=messages, tools=self._tool_specs
                    ),
                    timeout=self._per_step_timeout_s,
                )
            except TimeoutError:
                session.stopped_reason = StopReason.TIMEOUT
                log.warning("Agent step %d timed out", step)
                return session

            session.tokens_used += response.tokens_used

            if not response.tool_calls:
                session.final_text = response.text
                session.stopped_reason = StopReason.COMPLETE
                return session

            messages.append(response.to_assistant_message())

            # Execute tool calls sequentially so confirm prompts don't stack.
            denied = False
            for i, tc in enumerate(response.tool_calls):
                normalized = _normalize_tool_call(tc, i)
                step_record = await self._execute_one(normalized)
                session.steps.append(step_record)
                messages.append({
                    "role": "tool",
                    "tool_call_id": normalized.id,
                    "content": _truncate(step_record.result, _MESSAGE_RESULT_CAP),
                })
                if step_record.denied:
                    denied = True

            if denied:
                session.stopped_reason = StopReason.DENIED
                session.final_text = await self._force_synthesis(messages)
                return session

        session.stopped_reason = StopReason.STEP_CAP
        log.info("Agent hit step cap (%d)", self._max_steps)
        session.final_text = await self._force_synthesis(messages)
        return session

    async def _execute_one(self, tc: ToolCall) -> AgentStep:
        action = self._actions.get(tc.name)
        if action is None:
            msg = f"Unknown tool '{tc.name}'."
            self._log(f"\u2190 {msg}")
            return AgentStep(tc.name, tc.arguments, msg, 0.0)

        if action.requires_confirm:
            allowed = await self._confirm(tc.name, tc.arguments)
            if not allowed:
                msg = f"User denied {tc.name}."
                self._log(f"\u2190 {msg}")
                return AgentStep(tc.name, tc.arguments, msg, 0.0, denied=True)

        self._log(f"\u2192 {tc.name}({fmt_args(tc.arguments)})")
        start = time.monotonic()
        try:
            result = await asyncio.wait_for(
                action.execute(**tc.arguments),
                timeout=self._per_step_timeout_s,
            )
            duration_ms = (time.monotonic() - start) * 1000
            output = result.output if result.success else f"error: {result.output}"
            stored = _truncate(output, _SESSION_RESULT_CAP)
            self._log(f"\u2190 {_truncate(stored, 240)}")
            return AgentStep(tc.name, tc.arguments, stored, duration_ms)
        except TimeoutError:
            duration_ms = (time.monotonic() - start) * 1000
            msg = f"{tc.name} timed out after {self._per_step_timeout_s:.0f}s"
            self._log(f"\u2190 {msg}")
            return AgentStep(tc.name, tc.arguments, msg, duration_ms)
        except Exception as e:  # noqa: BLE001
            log.exception("Agent tool '%s' raised", tc.name)
            duration_ms = (time.monotonic() - start) * 1000
            msg = f"{tc.name} raised: {e}"
            self._log(f"\u2190 {msg}")
            return AgentStep(tc.name, tc.arguments, msg, duration_ms)

    async def _force_synthesis(self, messages: list[dict[str, Any]]) -> str:
        """Best-effort final text with tools disabled so a capped run still
        returns something useful instead of a bare trace."""
        try:
            response = await asyncio.wait_for(
                self._llm.generate_with_tools(messages=messages, tools=[]),
                timeout=self._per_step_timeout_s,
            )
            return response.text
        except Exception:
            log.exception("Forced synthesis failed")
            return ""


def _normalize_tool_call(tc: ToolCall, index: int) -> ToolCall:
    if tc.id:
        return tc
    return ToolCall(id=f"call_{index}", name=tc.name, arguments=tc.arguments)


def fmt_args(args: dict[str, Any], max_len: int = 80) -> str:
    """Shared with ConfirmModal so user-visible arg rendering stays consistent."""
    try:
        s = json.dumps(args, ensure_ascii=False)
    except (TypeError, ValueError):
        s = str(args)
    return _truncate(s, max_len)


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "\u2026"


_DEFAULT_SYSTEM_PROMPT = (
    "You are TokenPal in agent mode. The user gave you a goal. Use the "
    "available tools to investigate and then return a single final "
    "in-character summary answering the goal. Keep the final answer under "
    "4 sentences. Call tools only when they add real information — never "
    "echo the same tool twice with identical arguments. If a tool result "
    "answers the goal on its own, finish without another tool call."
)
