"""Shared call-site for AbstractAction — rate limits + usage recording.

Enforces ``action.rate_limit`` (fail-fast with a failed ActionResult, never
sleeps) and fires an optional ``on_call(name, duration_ms, success)`` hook
after every invocation. Scope is caller-defined: Brain builds a fresh
invoker per ``/agent`` run so rate-limit state resets between goals.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from typing import Any

from tokenpal.actions.base import AbstractAction, ActionResult

CallRecord = Callable[[str, float, bool], None]


class ToolInvoker:
    def __init__(self, on_call: CallRecord | None = None) -> None:
        self._on_call = on_call
        self._call_times: dict[str, deque[float]] = {}

    async def invoke(
        self, action: AbstractAction, arguments: dict[str, Any]
    ) -> ActionResult:
        limit = action.rate_limit
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
