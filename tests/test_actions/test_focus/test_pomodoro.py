"""Tests for PomodoroAction — phase transitions + teardown."""

from __future__ import annotations

import asyncio

from tokenpal.actions.focus.pomodoro import PomodoroAction


async def test_pomodoro_rejects_invalid_args() -> None:
    action = PomodoroAction({})
    r = await action.execute(work_min=0)
    assert not r.success
    r = await action.execute(break_min=9999)
    assert not r.success
    r = await action.execute(cycles=99)
    assert not r.success


async def test_pomodoro_transitions_through_phases() -> None:
    events: list[tuple[str, int]] = []

    def phase_msg(phase: str, cycle: int) -> str:
        events.append((phase, cycle))
        return f"{phase}-{cycle}"

    bubbles: list[str] = []
    # Use 1-minute settings but monkey-patch asyncio.sleep to speed it up.
    action = PomodoroAction(
        {"ui_callback": bubbles.append, "phase_message": phase_msg}
    )

    original_sleep = asyncio.sleep

    async def fast_sleep(_s: float) -> None:
        await original_sleep(0)

    import tokenpal.actions.focus.pomodoro as mod

    mod.asyncio.sleep = fast_sleep  # type: ignore[assignment]
    try:
        r = await action.execute(work_min=1, break_min=1, cycles=2)
        assert r.success
        # Let the background task run through all phases.
        assert action._task is not None
        await action._task
    finally:
        mod.asyncio.sleep = original_sleep  # type: ignore[assignment]

    assert [e[0] for e in events] == ["work", "break", "work", "break", "done"]
    assert bubbles == [
        "work-1",
        "break-1",
        "work-2",
        "break-2",
        "done-2",
    ]


async def test_pomodoro_teardown_cancels_task() -> None:
    action = PomodoroAction({})
    r = await action.execute(work_min=60, break_min=5, cycles=4)
    assert r.success
    task = action._task
    assert task is not None and not task.done()
    await action.teardown()
    assert action._task is None
    assert task.cancelled() or task.done()


async def test_pomodoro_rejects_concurrent_start() -> None:
    action = PomodoroAction({})
    try:
        r1 = await action.execute(work_min=60, break_min=5)
        assert r1.success
        r2 = await action.execute()
        assert not r2.success
        assert "already" in r2.output.lower()
    finally:
        await action.teardown()
