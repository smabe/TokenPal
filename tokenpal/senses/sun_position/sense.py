"""Sun position sense — emits on solar phase transitions (dawn, golden hour,
sunset, dusk, night). No network calls; coords come from [weather].
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable
from typing import Any, Literal

from tokenpal.senses.base import AbstractSense, SenseReading
from tokenpal.senses.registry import register_sense

log = logging.getLogger(__name__)

try:
    from astral import LocationInfo
    from astral.sun import sun as _astral_sun
    _HAS_ASTRAL = True
except ImportError:
    _HAS_ASTRAL = False

_GOLDEN_HOUR_MIN = 30

Phase = Literal[
    "night", "dawn", "golden_morning", "day", "golden_evening", "dusk",
]

_PHASE_SUMMARIES: dict[Phase, str] = {
    "dawn": "Civil twilight — sunrise approaching",
    "golden_morning": "Just past sunrise — golden hour",
    "day": "Sun is up",
    "golden_evening": "Golden hour — sunset in about 30 minutes",
    "dusk": "Just past sunset — civil twilight",
    "night": "Sun is down — full night",
}


def _classify(now: dt.datetime, sun_events: dict[str, dt.datetime]) -> Phase:
    dawn = sun_events["dawn"]
    sunrise = sun_events["sunrise"]
    sunset = sun_events["sunset"]
    dusk = sun_events["dusk"]
    golden_morning_end = sunrise + dt.timedelta(minutes=_GOLDEN_HOUR_MIN)
    golden_evening_start = sunset - dt.timedelta(minutes=_GOLDEN_HOUR_MIN)

    if now < dawn or now >= dusk:
        return "night"
    if now < sunrise:
        return "dawn"
    if now < golden_morning_end:
        return "golden_morning"
    if now < golden_evening_start:
        return "day"
    if now < sunset:
        return "golden_evening"
    return "dusk"


def _utc_now() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


@register_sense
class SunPositionSense(AbstractSense):
    sense_name = "sun_position"
    platforms = ("windows", "darwin", "linux")
    priority = 50
    poll_interval_s = 60.0
    reading_ttl_s = 1800.0

    def __init__(
        self,
        config: dict[str, Any],
        *,
        now_fn: Callable[[], dt.datetime] = _utc_now,
    ) -> None:
        super().__init__(config)
        self._now_fn = now_fn
        self._observer: Any = None
        self._cached_date: dt.date | None = None
        self._cached_events: dict[str, dt.datetime] | None = None
        self._prev_phase: Phase | None = None

    async def setup(self) -> None:
        if not _HAS_ASTRAL:
            log.warning("astral not installed — disabling sun_position")
            self.disable()
            return

        from tokenpal.config.loader import load_config
        weather = load_config().weather
        lat, lon = weather.latitude, weather.longitude
        if not lat and not lon:
            log.info("sun_position: no weather lat/lon configured — disabling")
            self.disable()
            return

        self._observer = LocationInfo(latitude=lat, longitude=lon).observer
        log.info("Sun position sense ready — %.1f, %.1f", lat, lon)

    async def poll(self) -> SenseReading | None:
        if not self.enabled or self._observer is None:
            return None

        now = self._now_fn()
        events = self._events_for(now.date())
        if events is None:
            return None

        phase = _classify(now, events)
        if phase == self._prev_phase:
            return None
        self._prev_phase = phase

        return self._reading(
            data={
                "phase": phase,
                "sunrise_utc": events["sunrise"].isoformat(),
                "sunset_utc": events["sunset"].isoformat(),
            },
            summary=_PHASE_SUMMARIES[phase],
            confidence=1.0,
        )

    def _events_for(self, date: dt.date) -> dict[str, dt.datetime] | None:
        if self._cached_date == date and self._cached_events is not None:
            return self._cached_events
        try:
            self._cached_events = _astral_sun(
                self._observer, date=date, tzinfo=dt.UTC,
            )
        except ValueError:
            # astral raises at extreme latitudes (polar day/night) — no events.
            log.debug("astral.sun() found no events for %s", date)
            self._cached_events = None
            return None
        self._cached_date = date
        return self._cached_events

    async def teardown(self) -> None:
        pass
