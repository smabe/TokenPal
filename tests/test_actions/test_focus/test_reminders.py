"""Tests for proactive reminder actions (stretch/water/eye/bedtime)."""

from __future__ import annotations

from datetime import datetime

from tokenpal.actions.focus.reminders import (
    BedtimeWindDownAction,
    EyeBreakAction,
    StretchReminderAction,
    WaterReminderAction,
    _make_bedtime_message_fn,
)
from tokenpal.brain.proactive import ProactiveScheduler


def _scheduler(paused: bool = False) -> tuple[ProactiveScheduler, list[str]]:
    bubbles: list[str] = []
    return (
        ProactiveScheduler(ui_callback=bubbles.append, is_paused=lambda: paused),
        bubbles,
    )


async def test_stretch_enable_disable() -> None:
    sched, bubbles = _scheduler()
    action = StretchReminderAction({"scheduler": sched})
    r = await action.execute(interval_min=1)
    assert r.success
    assert sched.is_registered("stretch_reminder")

    base = sched._nudges["stretch_reminder"].last_fired_at
    sched.tick(now=base + 0.0)
    assert bubbles == []
    sched.tick(now=base + 61.0)
    assert len(bubbles) == 1

    off = await action.execute(action="off")
    assert off.success
    assert not sched.is_registered("stretch_reminder")


async def test_water_default_interval() -> None:
    sched, _ = _scheduler()
    action = WaterReminderAction({"scheduler": sched})
    r = await action.execute()
    assert r.success
    assert "90" in r.output  # default interval_min


async def test_eye_break_rejects_bad_interval() -> None:
    sched, _ = _scheduler()
    action = EyeBreakAction({"scheduler": sched})
    r = await action.execute(interval_min=-1)
    assert not r.success


async def test_reminder_without_scheduler_fails() -> None:
    action = StretchReminderAction({})
    r = await action.execute()
    assert not r.success


async def test_proactive_pauses_during_sensitive_gate() -> None:
    """Integration: sensitive/conversation gate suppresses the reminder."""
    paused = {"v": True}
    bubbles: list[str] = []
    sched = ProactiveScheduler(
        ui_callback=bubbles.append, is_paused=lambda: paused["v"]
    )
    action = StretchReminderAction({"scheduler": sched})
    await action.execute(interval_min=1)

    base = sched._nudges["stretch_reminder"].last_fired_at
    sched.tick(now=base + 120.0)  # well past interval, still gated
    assert bubbles == []

    paused["v"] = False
    sched.tick(now=base + 121.0)  # gate open -> fires now
    assert len(bubbles) == 1


async def test_bedtime_wind_down_requires_target_time() -> None:
    sched, _ = _scheduler()
    action = BedtimeWindDownAction({"scheduler": sched})
    r = await action.execute()
    assert not r.success
    r = await action.execute(target_time="nope")
    assert not r.success


async def test_bedtime_message_fn_window() -> None:
    # Pretend "now" is 22:30; target 23:00 => 30 min away, inside window.
    from datetime import time as dtime

    fixed_now = datetime(2026, 1, 1, 22, 30, 0)
    fn = _make_bedtime_message_fn(
        dtime(23, 0), "wind down", now_fn=lambda: fixed_now
    )
    assert fn() == "wind down"

    # 21:00 now, 23:00 target => 2h away, outside window.
    fixed_early = datetime(2026, 1, 1, 21, 0, 0)
    fn2 = _make_bedtime_message_fn(
        dtime(23, 0), "wind down", now_fn=lambda: fixed_early
    )
    assert fn2() == ""

    # 23:30 now, 23:00 target => target treated as tomorrow, 23.5h away.
    fixed_late = datetime(2026, 1, 1, 23, 30, 0)
    fn3 = _make_bedtime_message_fn(
        dtime(23, 0), "wind down", now_fn=lambda: fixed_late
    )
    assert fn3() == ""


async def test_bedtime_enroll_and_cancel() -> None:
    sched, _ = _scheduler()
    action = BedtimeWindDownAction({"scheduler": sched})
    r = await action.execute(target_time="23:00", interval_min=30)
    assert r.success
    assert sched.is_registered("bedtime_wind_down")
    off = await action.execute(action="off")
    assert off.success
