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
from typing import Any, Protocol

from tokenpal.actions.base import AbstractAction
from tokenpal.actions.catalog import find_entry
from tokenpal.actions.invoker import ToolInvoker
from tokenpal.brain.stop_reason import AgentStopReason
from tokenpal.config.schema import AgentConfig
from tokenpal.llm.base import AbstractLLMBackend, LLMResponse, ToolCall

log = logging.getLogger(__name__)

_DEFAULTS = AgentConfig()

# Cap for each tool result *as fed back to the LLM*. Without this, a single
# verbose tool (system_info, list_processes) blows the prompt-context window
# well before the cumulative completion-token budget trips.
_MESSAGE_RESULT_CAP = 2048
# Cap for each tool result *kept in memory on AgentSession*. Wider so the
# user can still read the full output in the trace, tighter than unbounded.
_SESSION_RESULT_CAP = 4096


ConfirmFn = Callable[[str, dict[str, Any]], Awaitable[bool]]
SensitiveFn = Callable[[], bool]


class LogFn(Protocol):
    """Trace sink. ``persist=False`` keeps the line out of the chat log."""

    def __call__(
        self,
        text: str,
        *,
        markup: bool = False,
        url: str | None = None,
        persist: bool = True,
    ) -> None: ...


def noop_log(
    text: str, *, markup: bool = False, url: str | None = None, persist: bool = True,
) -> None:
    """LogFn that discards. Typed so it satisfies the Protocol at a join."""
    return None


_DESKTOP_CONTEXT_REASON = "desktop content is in context"
_SKIPPED_RESULT = f"skipped: {_DESKTOP_CONTEXT_REASON}"


@dataclass
class AgentStep:
    """One executed tool call or final-text step in an agent run."""

    tool_name: str
    arguments: dict[str, Any]
    result: str
    duration_ms: float
    denied: bool = False
    cached: bool = False


@dataclass
class AgentSession:
    """Result of a single /agent run."""

    goal: str
    steps: list[AgentStep] = field(default_factory=list)
    final_text: str = ""
    tokens_used: int = 0
    stopped_reason: AgentStopReason | str = ""
    started_at: float = field(default_factory=time.monotonic)
    desktop_content: bool = False

    @property
    def is_complete(self) -> bool:
        return self.stopped_reason == AgentStopReason.COMPLETE


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
        status_callback: Callable[[str], None] | None = None,
        tool_specs: list[dict[str, Any]] | None = None,
        max_steps: int = _DEFAULTS.max_steps,
        token_budget: int = _DEFAULTS.token_budget,
        per_step_timeout_s: float = _DEFAULTS.per_step_timeout_s,
        thinking: bool = _DEFAULTS.thinking,
        thinking_effort: str = _DEFAULTS.thinking_effort,
        max_tokens: int = _DEFAULTS.max_tokens,
        system_prompt: str | None = None,
        invoker: ToolInvoker | None = None,
    ) -> None:
        self._llm = llm
        self._actions = actions
        self._log = log_callback
        self._confirm = confirm_callback
        self._is_sensitive = is_sensitive
        self._status = status_callback
        self._tool_specs = (
            tool_specs
            if tool_specs is not None
            else [a.to_tool_spec() for a in actions.values()]
        )
        self._max_steps = max_steps
        self._token_budget = token_budget
        self._per_step_timeout_s = per_step_timeout_s
        self._thinking = thinking
        self._thinking_effort = thinking_effort
        self._max_tokens = max_tokens
        self._system_prompt = system_prompt or _DEFAULT_SYSTEM_PROMPT
        self._invoker = invoker or ToolInvoker()
        self._session: AgentSession | None = None

    @property
    def read_desktop_content(self) -> bool:
        """True once this run has executed a desktop-content tool. Survives a
        crash that discards the session object."""
        return self._session is not None and self._session.desktop_content

    def _trace(self, text: str) -> None:
        """Trace sink. Redaction is a property of the SESSION, not of the tool
        being called: once desktop content is in ``messages`` the model can
        copy it into any later call's arguments, so every line after the flag
        goes out unpersisted."""
        persist = not (self._session is not None and self._session.desktop_content)
        self._log(text, persist=persist)

    async def run(self, goal: str) -> AgentSession:
        session = AgentSession(goal=goal)
        self._session = session
        self._cache: dict[tuple[str, str], str] = {}
        self._gated_free_specs: list[dict[str, Any]] | None = None
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": goal},
        ]
        thinking = self._thinking

        for step in range(self._max_steps):
            if self._is_sensitive():
                session.stopped_reason = AgentStopReason.SENSITIVE
                log.info("Agent aborted mid-run: sensitive app detected")
                return session

            # Ollama sometimes returns tokens_used=0, so this is a soft cap —
            # if usage data is bad, step_cap still bounds the run.
            if session.tokens_used >= self._token_budget:
                session.stopped_reason = AgentStopReason.TOKEN_BUDGET
                log.info(
                    "Agent hit token budget (%d/%d)",
                    session.tokens_used,
                    self._token_budget,
                )
                session.final_text = await self._force_synthesis(
                    session, messages, thinking=thinking,
                )
                return session

            tools = self._tools_for(session)
            try:
                response = await self._step(
                    session, messages, thinking=thinking, tools=tools,
                )
                if (
                    thinking
                    and response.finish_reason == "length"
                    and not response.tool_calls
                    and not response.text.strip()
                ):
                    thinking = False
                    self._trace("(step truncated while thinking; continuing without thinking)")
                    response = await self._step(
                        session, messages, thinking=False, tools=tools,
                    )
            except TimeoutError:
                session.stopped_reason = AgentStopReason.TIMEOUT
                log.warning("Agent step %d timed out", step)
                return session

            if not response.tool_calls:
                session.final_text = response.text
                session.stopped_reason = AgentStopReason.COMPLETE
                return session

            messages.append(response.to_assistant_message())

            # Execute tool calls sequentially so confirm prompts don't stack.
            denied = False
            for i, tc in enumerate(response.tool_calls):
                normalized = _normalize_tool_call(tc, i)
                if session.desktop_content and (
                    _needs_consent(normalized.name)
                    or normalized.name in _PERSISTENT_SINKS
                ):
                    self._trace(
                        f"\u2190 skipped {normalized.name}: {_DESKTOP_CONTEXT_REASON}"
                    )
                    step_record = AgentStep(
                        normalized.name, normalized.arguments, _SKIPPED_RESULT, 0.0,
                    )
                else:
                    if self._status is not None:
                        try:
                            self._status(f"using {normalized.name}...")
                        except Exception:
                            log.exception("agent status_callback raised")
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
                session.stopped_reason = AgentStopReason.DENIED
                session.final_text = await self._force_synthesis(
                    session, messages, thinking=thinking,
                )
                return session
            # "using <tool>..." intentionally persists through the follow-up
            # LLM step; a fast gather would otherwise be overwritten before
            # the UI can render it. Next iteration resets it when the model
            # picks a new tool or the run completes.

        session.stopped_reason = AgentStopReason.STEP_CAP
        log.info("Agent hit step cap (%d)", self._max_steps)
        session.final_text = await self._force_synthesis(session, messages, thinking=thinking)
        return session

    async def _step(
        self,
        session: AgentSession,
        messages: list[dict[str, Any]],
        *,
        thinking: bool,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        response = await asyncio.wait_for(
            self._llm.generate_with_tools(
                messages=messages,
                tools=self._tool_specs if tools is None else tools,
                max_tokens=self._max_tokens,
                enable_thinking=thinking,
                thinking_effort=self._thinking_effort,
            ),
            timeout=self._per_step_timeout_s,
        )
        session.tokens_used += response.tokens_used
        if response.reasoning:
            if session.desktop_content:
                self._trace("\u2026 (reasoning hidden: desktop content in context)")
            else:
                self._trace(f"\u2026 {response.reasoning}")
        return response

    def _tools_for(self, session: AgentSession) -> list[dict[str, Any]] | None:
        """Tool list for the next step: ``None`` means the unfiltered set.
        Once desktop content is in context, every consent-gated (network)
        tool is dropped for the rest of the run, and so is every tool that
        would write model-authored text to a durable local sink."""
        if not session.desktop_content:
            return None
        if self._gated_free_specs is None:
            self._gated_free_specs = [
                spec for spec in self._tool_specs
                if not _needs_consent(spec["function"]["name"])
                and spec["function"]["name"] not in _PERSISTENT_SINKS
            ]
        return self._gated_free_specs

    async def _execute_one(self, tc: ToolCall) -> AgentStep:
        action = self._actions.get(tc.name)
        if action is None:
            msg = f"Unknown tool '{tc.name}'."
            self._trace(f"\u2190 {msg}")
            return AgentStep(tc.name, tc.arguments, msg, 0.0)

        redacted = action.reads_desktop_content
        if redacted and self._session is not None:
            # Set before the first trace line for this call, so even the tool's
            # own arguments go out unpersisted. This is the only writer: a
            # denied confirm still flags the session, which fails closed.
            self._session.desktop_content = True
        cache_eligible = action.cacheable and not action.requires_confirm and not redacted
        cache_key: tuple[str, str] | None = None
        if cache_eligible:
            cache_key = (tc.name, _stable_args_key(tc.arguments))
            hit = self._cache.get(cache_key)
            if hit is not None:
                self._trace(f"\u2190 (cached) {_truncate(hit, 240)}")
                return AgentStep(tc.name, tc.arguments, hit, 0.0, cached=True)

        if action.requires_confirm:
            allowed = await self._confirm(tc.name, tc.arguments)
            if not allowed:
                msg = f"User denied {tc.name}."
                self._trace(f"\u2190 {msg}")
                return AgentStep(tc.name, tc.arguments, msg, 0.0, denied=True)

        self._trace(f"\u2192 {tc.name}({fmt_args(tc.arguments)})")
        start = time.monotonic()
        try:
            result = await asyncio.wait_for(
                self._invoker.invoke(action, tc.arguments),
                timeout=self._per_step_timeout_s,
            )
            duration_ms = (time.monotonic() - start) * 1000
            output = result.output if result.success else f"error: {result.output}"
            stored = _truncate(output, _SESSION_RESULT_CAP)
            if redacted:
                self._trace(f"\u2190 [desktop content: {len(stored)} chars, not shown]")
            else:
                self._trace(f"\u2190 {_truncate(stored, 240)}")
            if cache_key is not None and result.success:
                self._cache[cache_key] = stored
            return AgentStep(
                tc.name, tc.arguments, stored, duration_ms,
            )
        except TimeoutError:
            duration_ms = (time.monotonic() - start) * 1000
            msg = f"{tc.name} timed out after {self._per_step_timeout_s:.0f}s"
            self._trace(f"\u2190 {msg}")
            return AgentStep(tc.name, tc.arguments, msg, duration_ms)
        except Exception as e:  # noqa: BLE001
            duration_ms = (time.monotonic() - start) * 1000
            if redacted:
                # The exception may quote the text the tool just read, so
                # neither the message nor the traceback may be logged.
                log.error("Agent tool '%s' raised %s", tc.name, type(e).__name__)
                msg = f"{tc.name} raised: {type(e).__name__} (details hidden)"
            else:
                log.exception("Agent tool '%s' raised", tc.name)
                msg = f"{tc.name} raised: {e}"
            self._trace(f"\u2190 {msg}")
            return AgentStep(tc.name, tc.arguments, msg, duration_ms)

    async def _force_synthesis(
        self, session: AgentSession, messages: list[dict[str, Any]], *, thinking: bool,
    ) -> str:
        """Best-effort final text with tools disabled so a capped run still
        returns something useful instead of a bare trace."""
        try:
            response = await self._step(session, messages, thinking=thinking, tools=[])
            return response.text
        except Exception:
            log.exception("Forced synthesis failed")
            return ""


# Tools whose arguments land in memory.db as model-authored text. The consent
# gate above only covers tools that reach the NETWORK, so these would otherwise
# let a desktop-content read be laundered into a durable local sink the
# contract in CLAUDE.md forbids. Their rows (`reminders`, `habit_log`,
# `mood_log`) are swept by neither _prune nor /clear, so the residue is
# permanent. Gated on BOTH sides: dropped from the advertised specs, and
# refused at execution so a sink called in the same batch as the read -- or
# re-emitted from a name the model saw earlier -- cannot slip through.
_PERSISTENT_SINKS = frozenset({"reminder", "habit_streak", "mood_check"})


def _needs_consent(name: str) -> bool:
    """True when the catalog gates *name* behind a consent category, i.e.
    it reaches the network. Tools with no catalog entry are not gated."""
    found = find_entry(name)
    return bool(found is not None and found[0].consent_category)


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


def _stable_args_key(args: dict[str, Any]) -> str:
    """Canonical JSON for cache keys — sort keys so {a:1,b:2} hashes the
    same as {b:2,a:1}. Fallback to repr for unhashable-but-json-serializable
    edge cases (rare; the registry accepts only JSON-schema types)."""
    try:
        return json.dumps(args, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(sorted(args.items()))


_DEFAULT_SYSTEM_PROMPT = (
    "You are TokenPal in agent mode. The user gave you a goal. Use the "
    "available tools to investigate and then return a single final "
    "in-character summary answering the goal. Keep the final answer under "
    "4 sentences. Call tools only when they add real information — never "
    "echo the same tool twice with identical arguments. If a tool result "
    "answers the goal on its own, finish without another tool call."
)
