"""Proactive reminder actions — stretch, water, eye-break, bedtime wind-down.

All four enroll a recurring nudge with the brain's ProactiveScheduler.
The scheduler handles the pause-during-conversation and pause-during-
sensitive-app gates, so the actions themselves stay dumb.

requires_confirm=True: the LLM can't silently turn these on behind the
user's back. Flip off by calling with action="off".
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.registry import register_action
from tokenpal.brain.proactive import ProactiveScheduler

log = logging.getLogger(__name__)

_MAX_INTERVAL_MIN = 24 * 60  # a day
_MIN_INTERVAL_MIN = 1

MessageFn = Callable[[], str]


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
    requires_confirm = True

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._scheduler = _coerce_scheduler(config)
        # Fallback message override hook (tests + voice customizations).
        self._message_override: MessageFn | None = config.get("message_fn")

    def _message_fn(self) -> MessageFn:
        if self._message_override is not None:
            return self._message_override
        msg = self.default_message
        return lambda: msg

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

        interval_min = int(kwargs.get("interval_min", self.default_interval_min))
        if interval_min < _MIN_INTERVAL_MIN or interval_min > _MAX_INTERVAL_MIN:
            return ActionResult(
                output=(
                    f"interval_min must be {_MIN_INTERVAL_MIN}-{_MAX_INTERVAL_MIN}."
                ),
                success=False,
            )

        self._scheduler.register(
            name=self.action_name,
            interval_s=interval_min * 60.0,
            message_fn=self._message_fn(),
        )
        return ActionResult(
            output=f"{self.action_name} enabled (every {interval_min} min)."
        )

    async def teardown(self) -> None:
        if self._scheduler is not None:
            self._scheduler.cancel(self.action_name)


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
    """At T-60 before target_time, start suggesting wrap-up."""

    action_name = "bedtime_wind_down"
    description = (
        "Starting 60 minutes before target_time (HH:MM 24h), "
        "nudge you to wrap up for the night."
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
    default_interval_min = 15  # re-nudge every 15 min inside the window
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
            target_t = datetime.strptime(target_raw.strip(), "%H:%M").time()
        except ValueError:
            return ActionResult(
                output="target_time must be HH:MM 24h (e.g. '23:00').",
                success=False,
            )

        interval_min = int(kwargs.get("interval_min", self.default_interval_min))
        if interval_min < _MIN_INTERVAL_MIN or interval_min > _MAX_INTERVAL_MIN:
            return ActionResult(
                output=(
                    f"interval_min must be {_MIN_INTERVAL_MIN}-{_MAX_INTERVAL_MIN}."
                ),
                success=False,
            )

        message_fn = _make_bedtime_message_fn(target_t, self.default_message)
        self._scheduler.register(
            name=self.action_name,
            interval_s=interval_min * 60.0,
            message_fn=message_fn,
        )
        return ActionResult(
            output=f"bedtime_wind_down armed for {target_raw.strip()}."
        )


def _make_bedtime_message_fn(
    target_time: Any,
    message: str,
    now_fn: Callable[[], datetime] | None = None,
) -> MessageFn:
    """Return a callable that yields the nudge only inside the 60-min window.

    Returning "" from a message_fn tells the scheduler "not now, try again";
    the scheduler keeps last_fired_at unchanged so the next tick re-checks.
    """
    clock = now_fn or datetime.now

    def _fn() -> str:
        now = clock()
        target_dt = now.replace(
            hour=target_time.hour,
            minute=target_time.minute,
            second=0,
            microsecond=0,
        )
        # If target already passed today, assume tomorrow's bedtime for the
        # next check — users arming it at 23:30 for a 22:00 bedtime really
        # mean tomorrow.
        if target_dt <= now:
            target_dt = target_dt + timedelta(days=1)
        delta = target_dt - now
        if timedelta(0) < delta <= timedelta(minutes=60):
            return message
        return ""

    return _fn
