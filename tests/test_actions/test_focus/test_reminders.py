"""Tests for proactive reminder actions (stretch/water/eye/bedtime)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from tokenpal.actions.focus.reminders import (
    BedtimeWindDownAction,
    EyeBreakAction,
    StretchReminderAction,
    WaterReminderAction,
)
from tokenpal.brain.memory import MemoryStore
from tokenpal.brain.proactive import ProactiveScheduler


def _scheduler(paused: bool = False) -> tuple[ProactiveScheduler, list[str]]:
    bubbles: list[str] = []
    return (
        ProactiveScheduler(ui_callback=bubbles.append, is_paused=lambda: paused),
        bubbles,
    )


def _armed_ids(sched: ProactiveScheduler) -> list[str]:
    return [n.id for n in sched.armed()]


async def test_stretch_enable_disable() -> None:
    sched, bubbles = _scheduler()
    action = StretchReminderAction({"scheduler": sched})
    r = await action.execute(interval_min=1)
    assert r.success
    assert _armed_ids(sched) == ["stretch_reminder"]
    assert sched.armed()[0].schedule.interval_s == 60.0

    due = sched.armed()[0].next_due_at
    sched.tick(now=due - 1.0)
    assert bubbles == []
    sched.tick(now=due)
    assert len(bubbles) == 1

    off = await action.execute(action="off")
    assert off.success
    assert _armed_ids(sched) == []


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

    due = sched.armed()[0].next_due_at
    sched.tick(now=due + 120.0)  # well past the deadline, still gated
    assert bubbles == []

    paused["v"] = False
    sched.tick(now=due + 121.0)  # gate open -> fires now
    assert len(bubbles) == 1


async def test_bedtime_wind_down_requires_target_time() -> None:
    sched, _ = _scheduler()
    action = BedtimeWindDownAction({"scheduler": sched})
    r = await action.execute()
    assert not r.success
    r = await action.execute(target_time="nope")
    assert not r.success


async def test_bedtime_arms_a_daily_schedule() -> None:
    sched, _ = _scheduler()
    action = BedtimeWindDownAction({"scheduler": sched})
    r = await action.execute(target_time="23:00")
    assert r.success
    assert _armed_ids(sched) == ["bedtime_wind_down"]

    nudge = sched.armed()[0]
    assert nudge.schedule.kind == "daily"
    assert (nudge.schedule.at_hour, nudge.schedule.at_minute) == (23, 0)
    due = datetime.fromtimestamp(nudge.next_due_at)
    assert (due.hour, due.minute) == (23, 0)

    off = await action.execute(action="off")
    assert off.success
    assert _armed_ids(sched) == []


async def test_shutdown_does_not_unpersist_an_armed_reminder(tmp_path: Path) -> None:
    """Brain._teardown_components awaits teardown() on every action at quit.

    An action-level teardown that cancels would delete every armed row from
    memory.db on every quit -- the exact outcome persistence exists to prevent.
    Nothing else in the suite catches a reintroduction.
    """
    store = MemoryStore(db_path=tmp_path / "brain.db")
    store.setup()
    try:
        sched = ProactiveScheduler(
            ui_callback=lambda _t: None, is_paused=lambda: False, memory=store
        )
        action = StretchReminderAction({"scheduler": sched})
        assert (await action.execute(interval_min=30)).success
        assert [r["id"] for r in store.list_reminders()] == ["stretch_reminder"]

        await action.teardown()

        assert [r["id"] for r in store.list_reminders()] == ["stretch_reminder"]
        assert _armed_ids(sched) == ["stretch_reminder"]
    finally:
        store.teardown()


async def test_bedtime_rejects_a_one_digit_minute() -> None:
    """'9:3' means 09:30 to a user and 09:03 to strptime."""
    sched, _ = _scheduler()
    action = BedtimeWindDownAction({"scheduler": sched})

    bad = await action.execute(target_time="9:3")
    assert not bad.success
    assert _armed_ids(sched) == []

    good = await action.execute(target_time="9:30")
    assert good.success
    assert sched.armed()[0].schedule.at_minute == 30
