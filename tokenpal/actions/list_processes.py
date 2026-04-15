"""List top processes by CPU then RSS (sensitive names stripped)."""

from __future__ import annotations

from typing import Any, ClassVar

import psutil

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.registry import register_action
from tokenpal.brain.personality import contains_sensitive_term

_TOP_N_CAP = 30


@register_action
class ListProcessesAction(AbstractAction):
    action_name = "list_processes"
    description = "List top processes by CPU% then memory. Sensitive app names are redacted."
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "top_n": {
                "type": "integer",
                "description": f"How many processes to return (capped at {_TOP_N_CAP}).",
            },
        },
    }
    safe = True
    requires_confirm = False

    async def execute(self, **kwargs: Any) -> ActionResult:
        top_n = kwargs.get("top_n", 10)
        if not isinstance(top_n, int) or top_n < 1:
            top_n = 10
        top_n = min(top_n, _TOP_N_CAP)

        procs: list[tuple[str, int, float, float]] = []
        for proc in psutil.process_iter(["name", "pid", "cpu_percent", "memory_info"]):
            info = proc.info
            name = info.get("name") or ""
            pid = info.get("pid") or 0
            cpu = float(info.get("cpu_percent") or 0.0)
            mem_info = info.get("memory_info")
            rss_mb = (mem_info.rss / (1024 * 1024)) if mem_info else 0.0
            procs.append((name, pid, cpu, rss_mb))

        procs.sort(key=lambda p: (p[2], p[3]), reverse=True)
        top = procs[:top_n]

        lines = []
        for name, pid, cpu, rss_mb in top:
            label = "something" if contains_sensitive_term(name) else name
            lines.append(f"{label} (pid {pid}): cpu {cpu:.1f}%, rss {rss_mb:.0f}MB")

        if not lines:
            return ActionResult(output="No processes reported.")
        return ActionResult(output="\n".join(lines))
