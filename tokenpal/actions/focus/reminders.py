"""Proactive reminder actions — stretch, water, eye-break, bedtime wind-down.

All four enroll a recurring nudge with the brain's ProactiveScheduler.
The scheduler handles the pause-during-conversation and pause-during-
sensitive-app gates, so the actions themselves stay dumb.

These are opt-in tools: `resolve_actions` never instantiates one unless its
name is in `[tools] enabled_tools`, so enabling it in the /tools picker IS the
user's consent. `requires_confirm` stays False -- a second modal would ask
again for permission already granted, and would also prompt on action="off",
which is a dialog to stop being nagged. Flip off by calling with action="off".
"""

from __future__ import annotations

import logging
from typing import Any

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.registry import register_action
from tokenpal.brain.proactive import ProactiveScheduler
from tokenpal.brain.schedule import MAX_INTERVAL_MIN, MIN_INTERVAL_MIN, Schedule

log = logging.getLogger(__name__)


def _coerce_scheduler(config: dict[str, Any]) -> ProactiveScheduler | None:
    """Pull the scheduler injected by Brain at action instantiation."""
    sched = config.get("scheduler")
    if isinstance(sched, ProactiveScheduler):
        return sched
    return None


class _ReminderBase(AbstractAction):
    """Shared enroll/cancel plumbing for the four proactive nudges."""

    default_interval_min: int = 60
    default_message: str = ""

    safe = False
    requires_confirm = False

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._scheduler = _coerce_scheduler(config)

    async def execute(self, **kwargs: Any) -> ActionResult:
        if self._scheduler is None:
            return ActionResult(
                output="Proactive reminders need a running brain; not available here.",
                success=False,
            )

        mode = str(kwargs.get("action", "on")).lower()
        if mode in ("off", "stop", "cancel"):
            removed = self._scheduler.cancel(self.action_name)
            return ActionResult(
                output=f"{self.action_name} cancelled." if removed
                else f"{self.action_name} was not active."
            )

        raw = kwargs.get("interval_min", self.default_interval_min)
        try:
            schedule = Schedule.interval_from_minutes(raw)
        except ValueError:
            return ActionResult(
                output=(
                    f"interval_min must be {MIN_INTERVAL_MIN}-{MAX_INTERVAL_MIN}."
                ),
                success=False,
            )

        self._scheduler.register(
            id=self.action_name,
            label=self.default_message,
            schedule=schedule,
        )
        assert schedule.interval_s is not None
        interval_min = int(schedule.interval_s) // 60
        return ActionResult(
            output=f"{self.action_name} enabled (every {interval_min} min)."
        )


@register_action
class StretchReminderAction(_ReminderBase):
    action_name = "stretch_reminder"
    description = "Turn on/off a recurring stretch nudge."
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["on", "off"]},
            "interval_min": {
                "type": "integer",
                "description": "Minutes between stretch nudges (default 60).",
            },
        },
    }
    default_interval_min = 60
    default_message = "Stretch break — stand up, roll your shoulders."


@register_action
class WaterReminderAction(_ReminderBase):
    action_name = "water_reminder"
    description = "Turn on/off a recurring hydration nudge."
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["on", "off"]},
            "interval_min": {
                "type": "integer",
                "description": "Minutes between water nudges (default 90).",
            },
        },
    }
    default_interval_min = 90
    default_message = "Drink some water. Your brain is mostly that."


@register_action
class EyeBreakAction(_ReminderBase):
    action_name = "eye_break"
    description = "20-20-20 rule: every 20 minutes, rest your eyes."
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["on", "off"]},
            "interval_min": {
                "type": "integer",
                "description": "Minutes between eye breaks (default 20).",
            },
        },
    }
    default_interval_min = 20
    default_message = "Look at something 20 feet away for 20 seconds."


@register_action
class BedtimeWindDownAction(_ReminderBase):
    """Fires once a day at target_time to suggest wrapping up."""

    action_name = "bedtime_wind_down"
    description = (
        "At target_time (HH:MM 24h), nudge you to wrap up for the night."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["on", "off"]},
            "target_time": {
                "type": "string",
                "description": "Bedtime in 24h HH:MM (e.g. '23:00').",
            },
        },
    }
    default_message = "Wind-down time. Start closing tabs."

    async def execute(self, **kwargs: Any) -> ActionResult:
        if self._scheduler is None:
            return ActionResult(
                output="Bedtime wind-down needs a running brain; not available here.",
                success=False,
            )

        mode = str(kwargs.get("action", "on")).lower()
        if mode in ("off", "stop", "cancel"):
            removed = self._scheduler.cancel(self.action_name)
            return ActionResult(
                output="bedtime_wind_down cancelled." if removed
                else "bedtime_wind_down was not active."
            )

        target_raw = kwargs.get("target_time")
        if not isinstance(target_raw, str) or not target_raw.strip():
            return ActionResult(
                output="target_time is required (e.g. '23:00').",
                success=False,
            )
        try:
            schedule = Schedule.daily_from_hhmm(target_raw)
        except ValueError:
            return ActionResult(
                output="target_time must be HH:MM 24h (e.g. '23:00').",
                success=False,
            )

        self._scheduler.register(
            id=self.action_name,
            label=self.default_message,
            schedule=schedule,
        )
        return ActionResult(
            output=f"bedtime_wind_down armed for {target_raw.strip()}."
        )
