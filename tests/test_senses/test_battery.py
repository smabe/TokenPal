"""Tests for the battery sense."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

from tokenpal.senses.battery.sense import BatterySense


@dataclass
class _Batt:
    percent: float
    power_plugged: bool
    secsleft: int = 0


def _make() -> BatterySense:
    return BatterySense({})


async def _poll_with(sense: BatterySense, battery):
    with patch("tokenpal.senses.battery.sense.psutil.sensors_battery", return_value=battery):
        return await sense.poll()


async def test_no_battery_disables_sense() -> None:
    sense = _make()
    r = await _poll_with(sense, None)
    assert r is None
    assert sense.enabled is False


async def test_first_poll_is_silent() -> None:
    sense = _make()
    r = await _poll_with(sense, _Batt(percent=80, power_plugged=True))
    assert r is None


async def test_unplug_transition_emits_reading() -> None:
    sense = _make()
    await _poll_with(sense, _Batt(percent=80, power_plugged=True))
    r = await _poll_with(sense, _Batt(percent=79, power_plugged=False))
    assert r is not None
    assert "unplugged" in r.summary or "battery" in r.summary.lower()
    assert r.data["plugged"] is False


async def test_low_battery_high_confidence() -> None:
    sense = _make()
    await _poll_with(sense, _Batt(percent=80, power_plugged=False))
    r = await _poll_with(sense, _Batt(percent=15, power_plugged=False))
    assert r is not None
    assert r.confidence >= 3.0
    assert r.data["state"] == "low"


async def test_critical_battery() -> None:
    sense = _make()
    await _poll_with(sense, _Batt(percent=80, power_plugged=False))
    r = await _poll_with(sense, _Batt(percent=3, power_plugged=False))
    assert r is not None
    assert r.data["state"] == "critical"
    assert "CRITICAL" in r.summary


async def test_no_emit_without_state_change() -> None:
    sense = _make()
    await _poll_with(sense, _Batt(percent=80, power_plugged=True))
    # Same state (still charging) should not emit
    r = await _poll_with(sense, _Batt(percent=81, power_plugged=True))
    assert r is None


async def test_full_charge_state() -> None:
    sense = _make()
    await _poll_with(sense, _Batt(percent=80, power_plugged=True))
    r = await _poll_with(sense, _Batt(percent=100, power_plugged=True))
    assert r is not None
    assert r.data["state"] == "full"
