"""Tests for the sunrise_sunset action."""

from __future__ import annotations

import pytest

from tokenpal.actions.utilities.sunrise_sunset import SunriseSunsetAction


async def test_sunrise_with_explicit_latlon() -> None:
    # San Francisco: valid lat/lon, should always produce three events.
    action = SunriseSunsetAction({})
    result = await action.execute(latitude=37.8, longitude=-122.4)
    assert result.success is True
    assert "Sunrise" in result.output
    assert "noon" in result.output
    assert "sunset" in result.output
    assert "37.8" in result.output


async def test_sunrise_unconfigured_latlon(monkeypatch: pytest.MonkeyPatch) -> None:
    # Zero/zero = weather not configured.
    monkeypatch.setattr(
        "tokenpal.actions.utilities.sunrise_sunset._load_default_latlon",
        lambda: (0.0, 0.0),
    )
    action = SunriseSunsetAction({})
    result = await action.execute()
    assert result.success is False
    assert "not configured" in result.output.lower()


async def test_sunrise_uses_config_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tokenpal.actions.utilities.sunrise_sunset._load_default_latlon",
        lambda: (40.7, -74.0),  # NYC
    )
    action = SunriseSunsetAction({})
    result = await action.execute()
    assert result.success is True
    assert "40.7" in result.output


async def test_sunrise_nonnumeric_latlon() -> None:
    action = SunriseSunsetAction({})
    result = await action.execute(latitude="north", longitude="south")
    assert result.success is False


async def test_sunrise_config_load_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> tuple[float, float]:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(
        "tokenpal.actions.utilities.sunrise_sunset._load_default_latlon", boom
    )
    action = SunriseSunsetAction({})
    result = await action.execute()
    assert result.success is False
    assert "weather config" in result.output.lower()


def test_sunrise_flags() -> None:
    assert SunriseSunsetAction.safe is True
    assert SunriseSunsetAction.requires_confirm is False
