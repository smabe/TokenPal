"""Battery sense — plug/unplug transitions, low-battery warnings.

Emits transition-only readings (poll returns None most of the time). Silent on
desktops where `psutil.sensors_battery()` returns None.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

import psutil

from tokenpal.senses.base import AbstractSense, SenseReading
from tokenpal.senses.registry import register_sense

log = logging.getLogger(__name__)

_LOW_PCT = 20.0
_CRITICAL_PCT = 5.0
_FULL_PCT = 99.0


def _classify(percent: float, plugged: bool) -> str:
    """Map (percent, plugged) to a coarse state tag used for transition checks."""
    if plugged and percent >= _FULL_PCT:
        return "full"
    if plugged:
        return "charging"
    if percent <= _CRITICAL_PCT:
        return "critical"
    if percent <= _LOW_PCT:
        return "low"
    return "on_battery"


_STATE_PHRASE: dict[str, str] = {
    "full":        "battery fully charged",
    "charging":    "plugged in, charging",
    "on_battery":  "unplugged, running on battery",
    "low":         "battery low, not plugged in",
    "critical":    "battery CRITICAL, plug in soon",
}


@register_sense
class BatterySense(AbstractSense):
    sense_name: ClassVar[str] = "battery"
    platforms: ClassVar[tuple[str, ...]] = ("windows", "darwin", "linux")
    priority: ClassVar[int] = 100
    poll_interval_s: ClassVar[float] = 30.0
    reading_ttl_s: ClassVar[float] = 300.0

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._prev_state: str | None = None
        self._missing_logged = False

    async def setup(self) -> None:
        pass

    async def poll(self) -> SenseReading | None:
        try:
            battery = psutil.sensors_battery()
        except Exception:
            # psutil occasionally raises on exotic power adapters; treat as no battery.
            return None

        if battery is None:
            if not self._missing_logged:
                log.debug("battery: no battery detected (desktop?) — sense will stay silent")
                self._missing_logged = True
            self.disable()
            return None

        percent = float(battery.percent)
        plugged = bool(battery.power_plugged)
        state = _classify(percent, plugged)

        if state == self._prev_state:
            return None

        prev = self._prev_state
        self._prev_state = state

        # Suppress startup noise — first observation has no transition context.
        if prev is None:
            return None

        summary = f"{_STATE_PHRASE[state]} ({percent:.0f}%)"
        confidence = 4.0 if state in ("low", "critical") else 2.0
        data: dict[str, Any] = {
            "percent": percent,
            "plugged": plugged,
            "state": state,
        }
        return self._reading(
            data=data,
            summary=summary,
            confidence=confidence,
            changed_from=_STATE_PHRASE.get(prev, prev),
        )

    async def teardown(self) -> None:
        pass
