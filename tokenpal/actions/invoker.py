"""Shared call-site for AbstractAction — path policy, rate limits, usage.

Resolves and contains every argument a tool declares in ``path_params``,
enforces ``action.rate_limit`` (fail-fast with a failed ActionResult, never
sleeps) and fires an optional ``on_call(name, duration_ms, success)`` hook
after every call that reaches the action. A path refusal returns before both,
so it spends no rate-limit slot and is not recorded. Scope is caller-defined: Brain builds a fresh
invoker per ``/agent`` run so rate-limit state resets between goals.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from typing import Any

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.util.paths import resolve_declared_path

CallRecord = Callable[[str, float, bool], None]


class ToolInvoker:
    def __init__(
        self,
        on_call: CallRecord | None = None,
        *,
        enforce_rate_limit: bool = True,
    ) -> None:
        self._on_call = on_call
        self._enforce_rate_limit = enforce_rate_limit
        self._call_times: dict[str, deque[float]] = {}

    async def invoke(
        self, action: AbstractAction, arguments: dict[str, Any]
    ) -> ActionResult:
        if action.path_params:
            contained = await self._contain_paths(action, arguments)
            if isinstance(contained, ActionResult):
                return contained
            arguments = contained

        # Nothing below this line may await before ``q.append``: the chat path
        # dispatches a whole round under ``gather``, and a suspension between
        # the length check and the append lets the entire round through.
        limit = action.rate_limit if self._enforce_rate_limit else None
        if limit is not None:
            now = time.monotonic()
            q = self._call_times.setdefault(action.action_name, deque())
            cutoff = now - limit.window_s
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= limit.max_calls:
                return ActionResult(
                    output=(
                        f"rate limit: {limit.max_calls} calls per "
                        f"{limit.window_s:g}s exceeded"
                    ),
                    success=False,
                )
            q.append(now)

        start = time.monotonic()
        result = await action.execute(**arguments)
        duration_ms = (time.monotonic() - start) * 1000.0
        if self._on_call is not None:
            try:
                self._on_call(action.action_name, duration_ms, result.success)
            except Exception:
                # Usage logging must never break a tool call.
                pass
        return result

    async def _contain_paths(
        self, action: AbstractAction, arguments: dict[str, Any]
    ) -> dict[str, Any] | ActionResult:
        """Substitute a ``ResolvedPath`` for each declared path, or refuse.

        Writes into a copy: the chat dispatcher logs ``tc.arguments`` after the
        call, and an absolute resolved target does not belong in that line.
        """
        contained = dict(arguments)
        for name in action.path_params:
            raw = contained.get(name)
            if not isinstance(raw, str) or not raw.strip():
                # Absent, blank, or the wrong type. The tool owns that refusal
                # — and a declared path can be optional, as grep_codebase's is.
                continue
            path, refusal = await resolve_declared_path(
                raw, action.path_roots, action.path_screen
            )
            if path is None:
                return ActionResult(output=refusal, success=False)
            contained[name] = path
        return contained
