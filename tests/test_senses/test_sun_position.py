"""Tests for the sun_position sense."""

from __future__ import annotations

import datetime as dt
from typing import Any
from unittest.mock import patch

import pytest

from tokenpal.config.schema import TokenPalConfig, WeatherConfig
from tokenpal.senses.sun_position.sense import (
    _GOLDEN_HOUR_MIN,
    _PHASE_SUMMARIES,
    SunPositionSense,
    _classify,
)


def _events(
    sunrise_h: int,
    sunset_h: int,
    *,
    dawn_offset_min: int = 30,
    dusk_offset_min: int = 30,
) -> dict[str, dt.datetime]:
    base = dt.datetime(2026, 4, 25, 0, 0, tzinfo=dt.UTC)
    sunrise = base.replace(hour=sunrise_h)
    sunset = base.replace(hour=sunset_h)
    return {
        "dawn": sunrise - dt.timedelta(minutes=dawn_offset_min),
        "sunrise": sunrise,
        "noon": base.replace(hour=(sunrise_h + sunset_h) // 2),
        "sunset": sunset,
        "dusk": sunset + dt.timedelta(minutes=dusk_offset_min),
    }


def _config_with_weather(lat: float, lon: float) -> TokenPalConfig:
    return TokenPalConfig(weather=WeatherConfig(latitude=lat, longitude=lon))


@pytest.mark.parametrize(
    "now_hour,now_minute,expected",
    [
        (3, 0, "night"),
        (5, 45, "dawn"),
        (6, 10, "golden_morning"),
        (10, 0, "day"),
        (17, 35, "golden_evening"),
        (18, 10, "dusk"),
        (22, 0, "night"),
    ],
)
def test_classify_phase(now_hour: int, now_minute: int, expected: str):
    events = _events(sunrise_h=6, sunset_h=18)
    now = dt.datetime(2026, 4, 25, now_hour, now_minute, tzinfo=dt.UTC)
    assert _classify(now, events) == expected


def test_classify_uses_golden_hour_constant():
    events = _events(sunrise_h=6, sunset_h=18)
    just_inside = events["sunrise"] + dt.timedelta(minutes=_GOLDEN_HOUR_MIN - 1)
    assert _classify(just_inside, events) == "golden_morning"
    just_after = events["sunrise"] + dt.timedelta(minutes=_GOLDEN_HOUR_MIN + 1)
    assert _classify(just_after, events) == "day"


def test_classify_at_exact_sunrise_is_golden_morning():
    events = _events(sunrise_h=6, sunset_h=18)
    assert _classify(events["sunrise"], events) == "golden_morning"


def test_classify_at_exact_sunset_is_dusk():
    events = _events(sunrise_h=6, sunset_h=18)
    assert _classify(events["sunset"], events) == "dusk"


def test_summary_present_for_every_phase():
    for phase in ("night", "dawn", "golden_morning", "day", "golden_evening", "dusk"):
        assert phase in _PHASE_SUMMARIES
        assert _PHASE_SUMMARIES[phase]


async def test_setup_disables_when_no_weather_lat_lon():
    sense = SunPositionSense({})
    with patch(
        "tokenpal.config.loader.load_config", return_value=_config_with_weather(0.0, 0.0),
    ):
        await sense.setup()
    assert sense.enabled is False


async def test_setup_enables_with_weather_lat_lon():
    sense = SunPositionSense({})
    with patch(
        "tokenpal.config.loader.load_config", return_value=_config_with_weather(41.1, -74.0),
    ):
        await sense.setup()
    assert sense.enabled is True


async def test_poll_emits_only_on_phase_transition():
    fixed_now = dt.datetime(2026, 4, 25, 12, 0, tzinfo=dt.UTC)
    sense = SunPositionSense({}, now_fn=lambda: fixed_now)
    with patch(
        "tokenpal.config.loader.load_config", return_value=_config_with_weather(41.1, -74.0),
    ):
        await sense.setup()

    events = _events(sunrise_h=6, sunset_h=18)
    sense._cached_date = fixed_now.astimezone(sense._solar_tz).date()
    sense._cached_events = events

    first = await sense.poll()
    assert first is not None
    assert first.data["phase"] == "day"
    assert await sense.poll() is None


async def test_poll_emits_after_phase_change():
    t1 = dt.datetime(2026, 4, 25, 12, 0, tzinfo=dt.UTC)
    t2 = dt.datetime(2026, 4, 25, 17, 35, tzinfo=dt.UTC)
    times = iter([t1, t2])
    sense = SunPositionSense({}, now_fn=lambda: next(times))
    with patch(
        "tokenpal.config.loader.load_config", return_value=_config_with_weather(41.1, -74.0),
    ):
        await sense.setup()
    sense._cached_date = t1.astimezone(sense._solar_tz).date()
    sense._cached_events = _events(sunrise_h=6, sunset_h=18)

    first = await sense.poll()
    second = await sense.poll()
    assert first is not None and first.data["phase"] == "day"
    assert second is not None and second.data["phase"] == "golden_evening"


async def test_poll_uses_observer_local_day_not_utc_day():
    """Regression: at lon -74 the local 'Sunday evening dusk' is on Monday UTC.
    Computing astral with date=UTC-day picks Saturday-evening's dusk, which
    misclassifies Sunday-morning local time as night."""
    sunday_morning_utc = dt.datetime(2026, 4, 26, 13, 5, tzinfo=dt.UTC)
    sense = SunPositionSense({}, now_fn=lambda: sunday_morning_utc)
    with patch(
        "tokenpal.config.loader.load_config", return_value=_config_with_weather(41.1, -74.0),
    ):
        await sense.setup()
    reading = await sense.poll()
    assert reading is not None
    assert reading.data["phase"] != "night"


async def test_poll_returns_none_when_disabled():
    sense = SunPositionSense({})
    with patch(
        "tokenpal.config.loader.load_config", return_value=_config_with_weather(0.0, 0.0),
    ):
        await sense.setup()
    assert await sense.poll() is None


async def test_setup_disables_when_astral_missing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "tokenpal.senses.sun_position.sense._HAS_ASTRAL", False,
    )
    sense = SunPositionSense({})
    await sense.setup()
    assert sense.enabled is False


def test_events_for_returns_none_at_polar_latitude():
    """At extreme latitude on certain dates astral raises ValueError; sense
    must absorb it and return None instead of crashing the brain loop."""
    sense = SunPositionSense({})
    sense._observer = type("Obs", (), {})()  # placeholder; _astral_sun mocked

    def raise_value_error(*a: Any, **kw: Any) -> Any:
        raise ValueError("no sun on this date")

    with patch("tokenpal.senses.sun_position.sense._astral_sun", raise_value_error):
        assert sense._events_for(dt.date(2026, 6, 21), dt.UTC) is None
