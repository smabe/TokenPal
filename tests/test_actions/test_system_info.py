"""Tests for the system_info action."""

from __future__ import annotations

from tokenpal.actions.system_info import SystemInfoAction


def _make_system_info():
    return SystemInfoAction({})


async def test_system_info_returns_stats():
    action = _make_system_info()
    result = await action.execute()
    assert result.success is True
    assert "CPU:" in result.output
    assert "RAM:" in result.output
    assert "Disk:" in result.output


async def test_system_info_has_percentages():
    action = _make_system_info()
    result = await action.execute()
    assert "%" in result.output
    assert "GB" in result.output
