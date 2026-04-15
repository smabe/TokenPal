"""Tests for the convert action (pint-backed unit conversions)."""

from __future__ import annotations

import pytest

from tokenpal.actions.utilities.convert import ConvertAction


@pytest.mark.parametrize(
    "value,from_unit,to_unit,expected_sub",
    [
        (10, "mi", "km", "16"),          # 10 miles = 16.09 km
        (1, "kg", "lb", "2.2"),          # 1 kg = 2.20462 lb
        (32, "degF", "degC", "0"),       # 32 F = 0 C
        (100, "degC", "degF", "212"),    # 100 C = 212 F
        (1, "hour", "minute", "60"),     # 1 h = 60 min
        (2.5, "meter", "cm", "250"),     # 2.5 m = 250 cm
    ],
)
async def test_convert_success(
    value: float, from_unit: str, to_unit: str, expected_sub: str
) -> None:
    action = ConvertAction({})
    result = await action.execute(value=value, from_unit=from_unit, to_unit=to_unit)
    assert result.success is True, result.output
    assert expected_sub in result.output


async def test_convert_unknown_unit() -> None:
    action = ConvertAction({})
    result = await action.execute(value=1, from_unit="flibberty", to_unit="km")
    assert result.success is False
    assert "unknown" in result.output.lower() or "unit" in result.output.lower()


async def test_convert_incompatible_units() -> None:
    action = ConvertAction({})
    result = await action.execute(value=1, from_unit="meter", to_unit="kilogram")
    assert result.success is False


async def test_convert_missing_args() -> None:
    action = ConvertAction({})
    result = await action.execute(value=1, from_unit="m")
    assert result.success is False


async def test_convert_nonnumeric_value() -> None:
    action = ConvertAction({})
    result = await action.execute(value="abc", from_unit="m", to_unit="km")
    assert result.success is False


async def test_convert_oversize_value() -> None:
    action = ConvertAction({})
    result = await action.execute(value=1e20, from_unit="m", to_unit="km")
    assert result.success is False
    assert "large" in result.output.lower()


def test_convert_action_flags() -> None:
    assert ConvertAction.safe is True
    assert ConvertAction.requires_confirm is False


def test_convert_tool_spec_valid() -> None:
    spec = ConvertAction({}).to_tool_spec()
    assert spec["type"] == "function"
    assert spec["function"]["name"] == "convert"
    required = spec["function"]["parameters"]["required"]
    assert set(required) == {"value", "from_unit", "to_unit"}
