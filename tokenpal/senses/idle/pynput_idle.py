"""Idle detection via pynput — tracks keyboard/mouse inactivity transitions."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from tokenpal.senses.base import AbstractSense, SenseReading
from tokenpal.senses.registry import register_sense

log = logging.getLogger(__name__)

# Idle tier thresholds (seconds)
_SHORT_IDLE = 120    # 2 min — no comment, normal pause
_MEDIUM_IDLE = 300   # 5 min — dry acknowledgment on return
_LONG_IDLE = 1800    # 30 min — dramatic return


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
        self._mouse_listener: Any = None
        self._kb_listener: Any = None

    async def setup(self) -> None:
        try:
            from pynput import keyboard, mouse
        except ImportError:
            log.warning("pynput not installed — disabling idle detection")
            self.disable()
            return

        self._mouse_listener = mouse.Listener(
            on_move=self._touch,
            on_click=self._touch,
            on_scroll=self._touch,
        )
        self._kb_listener = keyboard.Listener(
            on_press=self._touch,
        )

        self._mouse_listener.start()
        self._kb_listener.start()
        log.info("Idle sense: pynput listeners started")

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

        # Transition: active → idle
        if is_idle and not self._was_idle:
            self._was_idle = True
            self._idle_start = time.monotonic() - idle_seconds
            # Don't emit a reading when going idle — stay quiet
            return None

        # Transition: idle → active (the interesting moment)
        if not is_idle and self._was_idle:
            self._was_idle = False
            away_seconds = time.monotonic() - self._idle_start
            return self._return_reading(away_seconds)

        # Steady state — no reading (don't spam "still idle")
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
                "tier": (
                    "short" if away_seconds < _MEDIUM_IDLE
                    else "medium" if away_seconds < _LONG_IDLE
                    else "long"
                ),
            },
            summary=summary,
            confidence=confidence,
        )

    async def teardown(self) -> None:
        if self._mouse_listener is not None:
            self._mouse_listener.stop()
        if self._kb_listener is not None:
            self._kb_listener.stop()
        log.info("Idle sense: listeners stopped")
