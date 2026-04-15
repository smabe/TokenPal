"""Pomodoro action — work/break cycle with in-character phase announcements.

User-initiated (safe=True, requires_confirm=False). Starts a background
asyncio task that flips between work and break phases, emitting a speech
bubble through the brain's ui_callback at each transition. Stops after a
fixed number of cycles (default 4) or when teardown() is called.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.registry import register_action

log = logging.getLogger(__name__)

_MAX_MINUTES = 120
_MAX_CYCLES = 12

PhaseCallback = Callable[[str, int], str]
"""Takes (phase, cycle_num) and returns the bubble text. Lets tests stub
the voice layer without bringing up a PersonalityEngine."""


def _default_phase_message(phase: str, cycle_num: int) -> str:
    if phase == "work":
        return f"Work phase {cycle_num}. Lock in."
    if phase == "break":
        return f"Break time (cycle {cycle_num}). Stand up, look away."
    return f"Pomodoro finished after {cycle_num} cycles."


@register_action
class PomodoroAction(AbstractAction):
    action_name = "pomodoro"
    description = (
        "Start a pomodoro work/break cycle. Announces each phase in character."
    )
    parameters = {
        "type": "object",
        "properties": {
            "work_min": {
                "type": "integer",
                "description": "Work phase duration in minutes (default 25).",
            },
            "break_min": {
                "type": "integer",
                "description": "Break phase duration in minutes (default 5).",
            },
            "cycles": {
                "type": "integer",
                "description": "Number of work+break pairs to run (default 4).",
            },
        },
    }
    safe = True
    requires_confirm = False

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._task: asyncio.Task[None] | None = None
        # Injected by the brain at construction via action_configs. Falls
        # back to a no-op so unit tests don't need a ui_callback present.
        ui_cb: Callable[[str], None] | None = config.get("ui_callback")
        self._ui_callback: Callable[[str], None] = ui_cb or (lambda _t: None)
        phase_cb: PhaseCallback | None = config.get("phase_message")
        self._phase_message: PhaseCallback = phase_cb or _default_phase_message

    async def execute(self, **kwargs: Any) -> ActionResult:
        work_min = int(kwargs.get("work_min", 25))
        break_min = int(kwargs.get("break_min", 5))
        cycles = int(kwargs.get("cycles", 4))

        if work_min < 1 or work_min > _MAX_MINUTES:
            return ActionResult(
                output=f"work_min must be 1-{_MAX_MINUTES}.", success=False
            )
        if break_min < 1 or break_min > _MAX_MINUTES:
            return ActionResult(
                output=f"break_min must be 1-{_MAX_MINUTES}.", success=False
            )
        if cycles < 1 or cycles > _MAX_CYCLES:
            return ActionResult(
                output=f"cycles must be 1-{_MAX_CYCLES}.", success=False
            )

        if self._task and not self._task.done():
            return ActionResult(
                output="A pomodoro is already running. Cancel it first.",
                success=False,
            )

        self._task = asyncio.create_task(
            self._run_cycle(work_min, break_min, cycles)
        )
        return ActionResult(
            output=(
                f"Pomodoro started: {cycles} x ({work_min}m work + {break_min}m break)."
            )
        )

    async def _run_cycle(self, work_min: int, break_min: int, cycles: int) -> None:
        try:
            for i in range(1, cycles + 1):
                self._ui_callback(self._phase_message("work", i))
                await asyncio.sleep(work_min * 60)
                self._ui_callback(self._phase_message("break", i))
                await asyncio.sleep(break_min * 60)
            self._ui_callback(self._phase_message("done", cycles))
        except asyncio.CancelledError:
            log.debug("Pomodoro cancelled mid-cycle")
            raise

    async def teardown(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
