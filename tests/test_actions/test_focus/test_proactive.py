"""Tests for ProactiveScheduler — interval firing + pause gates."""

from __future__ import annotations

from tokenpal.brain.proactive import ProactiveScheduler


def _make(paused: bool = False) -> tuple[ProactiveScheduler, list[str]]:
    bubbles: list[str] = []
    sched = ProactiveScheduler(
        ui_callback=lambda t: bubbles.append(t),
        is_paused=lambda: paused,
    )
    return sched, bubbles


def test_scheduler_fires_after_interval() -> None:
    sched, bubbles = _make()
    sched.register("stretch", interval_s=10.0, message_fn=lambda: "stretch!")
    base = sched._nudges["stretch"].last_fired_at
    sched.tick(now=base)
    assert bubbles == []  # just registered; 0 < 10
    sched.tick(now=base + 9.9)
    assert bubbles == []
    sched.tick(now=base + 10.5)
    assert bubbles == ["stretch!"]
    # Next fire only after another full interval.
    sched.tick(now=base + 15.0)
    assert bubbles == ["stretch!"]
    sched.tick(now=base + 21.0)
    assert bubbles == ["stretch!", "stretch!"]


def test_scheduler_pauses_during_conversation() -> None:
    paused_state = {"paused": True}
    bubbles: list[str] = []
    sched = ProactiveScheduler(
        ui_callback=lambda t: bubbles.append(t),
        is_paused=lambda: paused_state["paused"],
    )
    sched.register("water", interval_s=5.0, message_fn=lambda: "drink")
    base = sched._nudges["water"].last_fired_at
    # 100 seconds pass while paused — no fires, no clock advance.
    sched.tick(now=base + 100.0)
    assert bubbles == []
    # Unpause; the nudge fires immediately because last_fired_at didn't advance
    # while the gate was shut.
    paused_state["paused"] = False
    sched.tick(now=base + 105.0)
    assert bubbles == ["drink"]


def test_scheduler_cancel() -> None:
    sched, bubbles = _make()
    sched.register("eye", interval_s=1.0, message_fn=lambda: "blink")
    assert sched.is_registered("eye")
    assert sched.cancel("eye") is True
    assert not sched.is_registered("eye")
    sched.tick()  # no registered nudges, no-op
    assert bubbles == []
    # Cancelling unknown name returns False, doesn't raise.
    assert sched.cancel("not-there") is False


def test_scheduler_empty_message_skips_without_advancing() -> None:
    """Bedtime semantics: message_fn returns '' until window is open."""
    sched, bubbles = _make()
    state = {"ready": False}
    sched.register(
        "bedtime",
        interval_s=5.0,
        message_fn=lambda: "wind down" if state["ready"] else "",
    )
    base = sched._nudges["bedtime"].last_fired_at
    sched.tick(now=base + 10.0)  # interval elapsed but fn returns ""
    assert bubbles == []
    sched.tick(now=base + 11.0)  # still empty
    assert bubbles == []
    state["ready"] = True
    sched.tick(now=base + 11.0)
    assert bubbles == ["wind down"]


def test_scheduler_one_shot_removes_after_fire() -> None:
    sched, bubbles = _make()
    sched.register("once", interval_s=1.0, message_fn=lambda: "hi", one_shot=True)
    base = sched._nudges["once"].last_fired_at
    sched.tick(now=base + 2.0)
    assert bubbles == ["hi"]
    assert not sched.is_registered("once")


def test_scheduler_invalid_interval_raises() -> None:
    sched, _ = _make()
    try:
        sched.register("bad", interval_s=0, message_fn=lambda: "x")
    except ValueError:
        pass
    else:
        raise AssertionError("zero interval should raise")
