"""Idle detection via pynput — tracks keyboard/mouse inactivity transitions."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from tokenpal.senses import _keyboard_bus
from tokenpal.senses.base import AbstractSense, SenseReading
from tokenpal.senses.registry import register_sense

log = logging.getLogger(__name__)

# Idle tier thresholds (seconds)
_SHORT_IDLE = 120    # 2 min — no comment, normal pause
_MEDIUM_IDLE = 300   # 5 min — dry acknowledgment on return
_LONG_IDLE = 1800    # 30 min — dramatic return

# Cadence for sustained-idle re-emission while the user stays AFK.
_SUSTAINED_EMIT_INTERVAL_S = 60.0


def _tier_for(away_seconds: float) -> str:
    if away_seconds < _MEDIUM_IDLE:
        return "short"
    if away_seconds < _LONG_IDLE:
        return "medium"
    return "long"


@register_sense
class PynputIdle(AbstractSense):
    sense_name = "idle"
    platforms = ("windows", "darwin", "linux")
    priority = 100
    poll_interval_s = 1.0

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._last_input: float = time.monotonic()
        self._lock = threading.Lock()
        self._was_idle: bool = False
        self._idle_start: float = 0.0
        self._last_sustained_emit: float = 0.0
        self._last_sustained_tier: str = ""
        self._mouse_listener: Any = None

    async def setup(self) -> None:
        try:
            from pynput import mouse
        except ImportError:
            log.warning("pynput not installed — disabling idle detection")
            self.disable()
            return

        _keyboard_bus._warmup_pynput_darwin_axtrust()

        self._mouse_listener = mouse.Listener(
            on_move=self._touch,
            on_click=self._touch,
            on_scroll=self._touch,
        )
        self._mouse_listener.start()
        _keyboard_bus.subscribe(self._touch)
        log.info("Idle sense: listeners started")

    def _touch(self, *_args: Any) -> None:
        """Update last-input timestamp. Called from pynput listener threads."""
        with self._lock:
            self._last_input = time.monotonic()

    async def poll(self) -> SenseReading | None:
        if not self.enabled:
            return None

        with self._lock:
            idle_seconds = time.monotonic() - self._last_input

        is_idle = idle_seconds >= _SHORT_IDLE

        now = time.monotonic()

        # Transition: active → idle
        if is_idle and not self._was_idle:
            self._was_idle = True
            self._idle_start = now - idle_seconds
            self._last_sustained_emit = now
            # Seed the tier so the cadence path doesn't false-trigger an
            # immediate emission on the very next poll.
            self._last_sustained_tier = _tier_for(idle_seconds)
            # Don't emit a reading when going idle — stay quiet
            return None

        # Transition: idle → active (the interesting moment)
        if not is_idle and self._was_idle:
            self._was_idle = False
            self._last_sustained_emit = 0.0
            self._last_sustained_tier = ""
            away_seconds = now - self._idle_start
            return self._return_reading(away_seconds)

        # Sustained idle: re-emit on cadence or on tier-bump so the brain has
        # an explicit "user is parked" signal instead of going dark.
        if is_idle and self._was_idle:
            away_seconds = now - self._idle_start
            tier = _tier_for(away_seconds)
            tier_changed = tier != self._last_sustained_tier
            cadence_due = now - self._last_sustained_emit >= _SUSTAINED_EMIT_INTERVAL_S
            if tier_changed or cadence_due:
                self._last_sustained_emit = now
                self._last_sustained_tier = tier
                return self._sustained_reading(away_seconds, tier)

        return None

    def _return_reading(self, away_seconds: float) -> SenseReading | None:
        """Build a reading for the return-from-idle transition."""
        away_min = away_seconds / 60

        if away_seconds < _MEDIUM_IDLE:
            # Short idle (2-5 min): low-interest, brief
            summary = f"User returned after a {int(away_min)}-minute break"
            confidence = 0.3
        elif away_seconds < _LONG_IDLE:
            # Medium idle (5-30 min): worth a dry comment
            summary = f"User returned after {int(away_min)} minutes away"
            confidence = 0.7
        else:
            # Long idle (30+ min): dramatic return
            if away_min >= 60:
                hours = away_seconds / 3600
                summary = f"User returned after being away for {hours:.1f} hours"
            else:
                summary = f"User returned after {int(away_min)} minutes away"
            confidence = 1.0

        return self._reading(
            data={
                "event": "returned",
                "away_seconds": round(away_seconds),
                "away_minutes": round(away_min, 1),
                "tier": _tier_for(away_seconds),
            },
            summary=summary,
            confidence=confidence,
        )

    def _sustained_reading(self, away_seconds: float, tier: str) -> SenseReading:
        """Build a 'user is still idle' reading for sustained AFK stretches.

        Confidence is tier-scaled and stays below the matching return-from-idle
        confidence so a sharp return still wins topic competition (return gets
        a 1.5× change_bonus on top).
        """
        away_min = away_seconds / 60

        if tier == "short":
            confidence = 0.3
        elif tier == "medium":
            confidence = 0.5
        else:
            confidence = 0.7

        if away_seconds >= 3600:
            hours = away_seconds / 3600
            summary = f"User has been idle for {hours:.1f} hours"
        else:
            summary = f"User has been idle for {int(away_min)} minutes"

        return self._reading(
            data={
                "event": "sustained",
                "idle_seconds": round(away_seconds),
                "idle_minutes": round(away_min, 1),
                "tier": tier,
            },
            summary=summary,
            confidence=confidence,
        )

    async def teardown(self) -> None:
        if self._mouse_listener is not None:
            self._mouse_listener.stop()
        _keyboard_bus.unsubscribe(self._touch)
        log.info("Idle sense: listeners stopped")
