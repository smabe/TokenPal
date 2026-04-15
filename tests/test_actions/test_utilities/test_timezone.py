"""Tests for the timezone action."""

from __future__ import annotations

import pytest

from tokenpal.actions.utilities._timezone_cities import lookup, normalize
from tokenpal.actions.utilities.timezone import TimezoneAction


@pytest.mark.parametrize(
    "city,expected_zone",
    [
        ("Tokyo", "Asia/Tokyo"),
        ("new york", "America/New_York"),
        ("Los-Angeles", "America/Los_Angeles"),
        ("LONDON", "Europe/London"),
        ("Sao Paulo", "America/Sao_Paulo"),
        ("Sydney", "Australia/Sydney"),
    ],
)
def test_lookup_known_cities(city: str, expected_zone: str) -> None:
    assert lookup(city) == expected_zone


def test_lookup_unknown_city_returns_none() -> None:
    assert lookup("Atlantis") is None


def test_normalize_collapses_separators() -> None:
    assert normalize("  Los-Angeles  ") == "los angeles"
    assert normalize("New_York") == "new york"


async def test_timezone_action_known_city() -> None:
    action = TimezoneAction({})
    result = await action.execute(city="Tokyo")
    assert result.success is True
    assert "Asia/Tokyo" in result.output
    assert "UTC+" in result.output or "UTC-" in result.output


async def test_timezone_action_case_insensitive() -> None:
    action = TimezoneAction({})
    result = await action.execute(city="PARIS")
    assert result.success is True
    assert "Europe/Paris" in result.output


async def test_timezone_action_unknown_city() -> None:
    action = TimezoneAction({})
    result = await action.execute(city="Atlantis")
    assert result.success is False
    assert "not in table" in result.output


async def test_timezone_action_empty_city() -> None:
    action = TimezoneAction({})
    result = await action.execute(city="")
    assert result.success is False


async def test_timezone_action_missing_city() -> None:
    action = TimezoneAction({})
    result = await action.execute()
    assert result.success is False


def test_timezone_flags() -> None:
    assert TimezoneAction.safe is True
    assert TimezoneAction.requires_confirm is False
