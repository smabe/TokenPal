"""Tests for the moon_phase action."""

from __future__ import annotations

import pytest

from tokenpal.actions.utilities.moon_phase import (
    MoonPhaseAction,
    _illumination_pct,
    _phase_name,
)


@pytest.mark.parametrize(
    "phase_value,expected_name",
    [
        (0.0, "new"),
        (3.5, "waxing crescent"),
        (7.0, "first quarter"),
        (10.0, "waxing gibbous"),
        (14.0, "full"),
        (17.0, "waning gibbous"),
        (21.0, "last quarter"),
        (25.0, "waning crescent"),
        (27.5, "new"),
    ],
)
def test_phase_name_buckets(phase_value: float, expected_name: str) -> None:
    assert _phase_name(phase_value) == expected_name


def test_illumination_bounds() -> None:
    assert _illumination_pct(0.0) == 0
    # Full moon ~= 100% illuminated
    assert _illumination_pct(14.0) >= 99
    # Quarters ~= 50%
    assert 45 <= _illumination_pct(7.0) <= 55
    assert 45 <= _illumination_pct(21.0) <= 55


async def test_moon_phase_known_date() -> None:
    # 2024-08-19 was a full moon (Sturgeon moon). Verify we land in the full
    # bucket (allowing +/- 1 day slop in astral's phase scale).
    action = MoonPhaseAction({})
    result = await action.execute(date="2024-08-19")
    assert result.success is True
    assert "2024-08-19" in result.output
    assert "full" in result.output or "gibbous" in result.output


async def test_moon_phase_new_moon() -> None:
    # 2024-08-04 was a new moon.
    action = MoonPhaseAction({})
    result = await action.execute(date="2024-08-04")
    assert result.success is True
    assert "new" in result.output or "crescent" in result.output


async def test_moon_phase_default_today() -> None:
    action = MoonPhaseAction({})
    result = await action.execute()
    assert result.success is True
    assert "illuminated" in result.output


async def test_moon_phase_bad_date_format() -> None:
    action = MoonPhaseAction({})
    result = await action.execute(date="Aug 19 2024")
    assert result.success is False
    assert "YYYY-MM-DD" in result.output


async def test_moon_phase_empty_date_defaults_today() -> None:
    action = MoonPhaseAction({})
    result = await action.execute(date="")
    assert result.success is True


def test_moon_phase_flags() -> None:
    assert MoonPhaseAction.safe is True
    assert MoonPhaseAction.requires_confirm is False
