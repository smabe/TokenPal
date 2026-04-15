"""User-initiated logging actions — hydration, habit streaks, mood checks.

All three go through MemoryStore so we keep the 0o600 privacy posture.
Safe by design: only local writes, no network, no LLM tool-calling side
effects beyond reading the store back out.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.registry import register_action
from tokenpal.brain.memory import MemoryStore

log = logging.getLogger(__name__)

_MAX_OZ_PER_ENTRY = 128.0  # one-gallon-ish sanity cap


def _coerce_memory(config: dict[str, Any]) -> MemoryStore | None:
    m = config.get("memory")
    if isinstance(m, MemoryStore):
        return m
    return None


@register_action
class HydrationLogAction(AbstractAction):
    action_name = "hydration_log"
    description = "Log fluid intake (oz) and return today's running total."
    parameters = {
        "type": "object",
        "properties": {
            "amount_oz": {
                "type": "number",
                "description": "How many fluid ounces to add to today's total.",
            },
        },
        "required": ["amount_oz"],
    }
    safe = True
    requires_confirm = False

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._memory = _coerce_memory(config)

    async def execute(self, **kwargs: Any) -> ActionResult:
        if self._memory is None:
            return ActionResult(
                output="Memory store unavailable; can't log hydration.",
                success=False,
            )
        raw = kwargs.get("amount_oz")
        try:
            amount = float(raw) if raw is not None else 0.0
        except (TypeError, ValueError):
            return ActionResult(output="amount_oz must be a number.", success=False)
        if amount <= 0 or amount > _MAX_OZ_PER_ENTRY:
            return ActionResult(
                output=f"amount_oz must be 0 < x <= {_MAX_OZ_PER_ENTRY}.",
                success=False,
            )
        self._memory.log_hydration(amount)
        total = self._memory.get_hydration_today()
        return ActionResult(
            output=f"Logged {amount:.0f}oz. Today's total: {total:.0f}oz."
        )


@register_action
class HabitStreakAction(AbstractAction):
    action_name = "habit_streak"
    description = (
        "Log (or query) a named habit for today and return current + longest streak."
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Habit name."},
            "log": {
                "type": "boolean",
                "description": "If true, mark the habit done for today before reporting.",
            },
        },
        "required": ["name"],
    }
    safe = True
    requires_confirm = False

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._memory = _coerce_memory(config)

    async def execute(self, **kwargs: Any) -> ActionResult:
        if self._memory is None:
            return ActionResult(
                output="Memory store unavailable; can't track habits.",
                success=False,
            )
        name = str(kwargs.get("name", "")).strip()
        if not name:
            return ActionResult(output="Habit name required.", success=False)
        if len(name) > 64:
            return ActionResult(output="Habit name too long (max 64).", success=False)

        should_log = bool(kwargs.get("log", True))
        if should_log:
            self._memory.log_habit(name)
        current, longest = self._memory.get_habit_streak(name)
        return ActionResult(
            output=(
                f"'{name}': {current} day streak (longest {longest})."
            )
        )


@register_action
class MoodCheckAction(AbstractAction):
    action_name = "mood_check"
    description = (
        "Prompt a quick mood check. If a mood is supplied within 60s via "
        "the 'mood' argument, record it. Otherwise just show the prompt."
    )
    parameters = {
        "type": "object",
        "properties": {
            "mood": {
                "type": "string",
                "description": (
                    "Optional mood label to record (e.g. 'tired', 'focused')."
                ),
            },
        },
    }
    safe = True
    requires_confirm = False

    # 60-second debounce after a prompt-only call: late mood submissions
    # after that window are dropped to avoid stale tagging.
    _SUBMIT_WINDOW_S = 60.0

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._memory = _coerce_memory(config)
        self._last_prompt_at: float = 0.0
        self._lock = asyncio.Lock()

    async def execute(self, **kwargs: Any) -> ActionResult:
        mood_raw = kwargs.get("mood")
        async with self._lock:
            now = time.monotonic()
            if mood_raw is None or str(mood_raw).strip() == "":
                self._last_prompt_at = now
                return ActionResult(
                    output="How are you feeling? (say one word: tired, focused, stressed, ok...)"
                )

            mood = str(mood_raw).strip().lower()
            if len(mood) > 32:
                return ActionResult(output="Mood too long (max 32).", success=False)

            within_window = (now - self._last_prompt_at) <= self._SUBMIT_WINDOW_S
            if not within_window:
                return ActionResult(
                    output=(
                        f"Mood '{mood}' noted but not stored "
                        "(no recent prompt; run mood_check first)."
                    )
                )
            if self._memory is None:
                return ActionResult(
                    output=f"Memory unavailable; can't save mood '{mood}'.",
                    success=False,
                )
            self._memory.log_mood(mood)
            self._last_prompt_at = 0.0
            return ActionResult(output=f"Mood logged: {mood}.")
