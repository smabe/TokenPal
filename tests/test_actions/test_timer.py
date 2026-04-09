"""Tests for the timer action."""

from __future__ import annotations

import asyncio

from tokenpal.actions.timer import TimerAction


def _make_timer():
    return TimerAction({})


async def test_timer_set_basic():
    timer = _make_timer()
    result = await timer.execute(label="test", seconds=5)
    assert result.success is True
    assert "test" in result.output
    assert "5s" in result.output
    await timer.teardown()


async def test_timer_display_minutes():
    timer = _make_timer()
    result = await timer.execute(label="long", seconds=90)
    assert "1m30s" in result.output
    await timer.teardown()


async def test_timer_display_exact_minutes():
    timer = _make_timer()
    result = await timer.execute(label="round", seconds=120)
    assert "2m" in result.output
    await timer.teardown()


async def test_timer_rejects_negative():
    timer = _make_timer()
    result = await timer.execute(label="bad", seconds=-1)
    assert result.success is False


async def test_timer_rejects_too_long():
    timer = _make_timer()
    result = await timer.execute(label="long", seconds=9999)
    assert result.success is False
    assert "3600" in result.output


async def test_timer_max_active():
    timer = _make_timer()
    for i in range(5):
        r = await timer.execute(label=f"t{i}", seconds=300)
        assert r.success is True

    r = await timer.execute(label="overflow", seconds=10)
    assert r.success is False
    assert "max" in r.output.lower()
    await timer.teardown()


async def test_timer_replace_existing():
    timer = _make_timer()
    await timer.execute(label="dup", seconds=300)
    assert len(timer._active) == 1

    await timer.execute(label="dup", seconds=60)
    assert len(timer._active) == 1
    await timer.teardown()


async def test_timer_teardown_cancels_all():
    timer = _make_timer()
    await timer.execute(label="a", seconds=300)
    await timer.execute(label="b", seconds=300)
    assert len(timer._active) == 2

    await timer.teardown()
    assert len(timer._active) == 0


async def test_timer_fires_and_removes():
    timer = _make_timer()
    await timer.execute(label="quick", seconds=1)
    assert "quick" in timer._active

    await asyncio.sleep(1.2)
    assert "quick" not in timer._active
    await timer.teardown()
