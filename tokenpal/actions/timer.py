"""Timer action — set a named timer that fires after N seconds."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.registry import register_action

log = logging.getLogger(__name__)

_MAX_SECONDS = 3600
_MAX_ACTIVE = 5


@register_action
class TimerAction(AbstractAction):
    action_name = "timer"
    description = "Set a countdown timer that logs when it finishes."
    parameters = {
        "type": "object",
        "properties": {
            "label": {
                "type": "string",
                "description": "Short label for the timer (e.g. 'coffee', 'standup')",
            },
            "seconds": {
                "type": "integer",
                "description": "Duration in seconds",
            },
        },
        "required": ["label", "seconds"],
    }
    safe = True
    requires_confirm = False

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._active: dict[str, asyncio.Task[None]] = {}

    async def execute(self, **kwargs: Any) -> ActionResult:
        label = kwargs.get("label", "timer")
        seconds = kwargs.get("seconds", 60)

        if not isinstance(seconds, int) or seconds < 1:
            return ActionResult(output="Seconds must be a positive integer.", success=False)
        if seconds > _MAX_SECONDS:
            return ActionResult(output=f"Max timer is {_MAX_SECONDS}s (1 hour).", success=False)
        if len(self._active) >= _MAX_ACTIVE:
            return ActionResult(output=f"Too many timers (max {_MAX_ACTIVE}).", success=False)

        async def _fire() -> None:
            await asyncio.sleep(seconds)
            self._active.pop(label, None)
            log.info("Timer '%s' fired after %ds", label, seconds)

        if label in self._active:
            self._active[label].cancel()

        self._active[label] = asyncio.create_task(_fire())

        if seconds >= 60:
            display = f"{seconds // 60}m{seconds % 60}s" if seconds % 60 else f"{seconds // 60}m"
        else:
            display = f"{seconds}s"

        return ActionResult(output=f"Timer '{label}' set for {display}.")

    async def teardown(self) -> None:
        for task in self._active.values():
            task.cancel()
        self._active.clear()
