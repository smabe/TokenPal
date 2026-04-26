"""Idle detection via pynput — tracks keyboard/mouse inactivity transitions."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from tokenpal.senses import _keyboard_bus
from tokenpal.senses._keyboard_bus import Subscriber
from tokenpal.senses.base import AbstractSense, SenseReading
from tokenpal.senses.registry import register_sense

log = logging.getLogger(__name__)

# Idle tier thresholds (seconds)
_SHORT_IDLE = 120    # 2 min — no comment, normal pause
_MEDIUM_IDLE = 300   # 5 min — dry acknowledgment on return
_LONG_IDLE = 1800    # 30 min — dramatic return

# Cadence for sustained-idle re-emission while the user stays AFK.
_SUSTAINED_EMIT_INTERVAL_S = 60.0

# Return-from-idle debounce. A single phantom event (BT mouse jitter, system-
# injected cursor wake, notification banner) used to flip "User returned after
# N minutes away" instantly. We now require either ≥2 events or ≥5s of
# continued activity before committing the return.
_RETURN_DEBOUNCE_SUSTAINED_S = 5.0
_RETURN_DEBOUNCE_MIN_EVENTS = 2


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
        self._event_count: int = 0
        self._last_input_source: str = ""
        # (first_input_at, event_count_snapshot) for an unconfirmed return.
        self._pending_return: tuple[float, int] | None = None
        # Last input timestamp we already classified as a phantom — lets us
        # ignore it without losing was_idle state.
        self._discarded_input_at: float = -1.0
        self._kbd_subscriber: Subscriber | None = None

    async def setup(self) -> None:
        try:
            from pynput import mouse
        except ImportError:
            log.warning("pynput not installed — disabling idle detection")
            self.disable()
            return

        _keyboard_bus._warmup_pynput_darwin_axtrust()

        self._mouse_listener = mouse.Listener(
            on_move=lambda *_a: self._touch("mouse_move"),
            on_click=lambda *_a: self._touch("mouse_click"),
            on_scroll=lambda *_a: self._touch("mouse_scroll"),
        )
        self._mouse_listener.start()
        self._kbd_subscriber = lambda: self._touch("keyboard")
        _keyboard_bus.subscribe(self._kbd_subscriber)
        log.info("Idle sense: listeners started")

    def _touch(self, source: str = "test") -> None:
        """Update last-input timestamp. Called from pynput listener threads."""
        with self._lock:
            self._last_input = time.monotonic()
            self._event_count += 1
            self._last_input_source = source

    async def poll(self) -> SenseReading | None:
        if not self.enabled:
            return None

        with self._lock:
            last_input = self._last_input
            event_count = self._event_count

        now = time.monotonic()

        # Resolve any open pending-return decision before reclassifying state.
        if self._pending_return is not None:
            first_input_at, count_snap = self._pending_return
            elapsed = now - first_input_at
            new_events = event_count - count_snap

            sustained_ok = elapsed >= _RETURN_DEBOUNCE_SUSTAINED_S and new_events >= 1
            burst_ok = new_events >= _RETURN_DEBOUNCE_MIN_EVENTS - 1

            if sustained_ok or burst_ok:
                self._was_idle = False
                self._pending_return = None
                self._discarded_input_at = -1.0
                self._last_sustained_emit = 0.0
                self._last_sustained_tier = ""
                return self._return_reading(first_input_at - self._idle_start)

            if elapsed < _RETURN_DEBOUNCE_SUSTAINED_S:
                return None

            # Decision window expired without enough activity — phantom.
            self._discarded_input_at = first_input_at
            self._pending_return = None
            log.debug(
                "idle: phantom return discarded "
                "(source=%s, %.1fs since with %d follow-ups)",
                self._last_input_source or "?", elapsed, new_events,
            )

        # Treat a discarded phantom as if it never advanced last_input.
        effective_last_input = (
            self._idle_start
            if last_input == self._discarded_input_at
            else last_input
        )
        idle_seconds = now - effective_last_input
        is_idle = idle_seconds >= _SHORT_IDLE

        # Transition: active → idle
        if is_idle and not self._was_idle:
            self._was_idle = True
            self._idle_start = now - idle_seconds
            self._last_sustained_emit = now
            self._last_sustained_tier = _tier_for(idle_seconds)
            return None

        # Possible idle → active: open pending window, confirm next poll.
        if not is_idle and self._was_idle:
            self._pending_return = (last_input, event_count)
            return None

        # Sustained idle: re-emit on cadence or tier-bump.
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
        if self._kbd_subscriber is not None:
            _keyboard_bus.unsubscribe(self._kbd_subscriber)
            self._kbd_subscriber = None
        log.info("Idle sense: listeners stopped")
