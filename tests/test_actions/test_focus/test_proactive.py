"""Tests for ProactiveScheduler — wall-clock firing, pause gates, persistence.

The scheduler is a pure clock: it decides what is due, writes the fire
through, and returns it. It delivers nothing — `Brain._fire_due_nudges` does
— so a fire is observed here as a return value, never as a bubble.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

from tokenpal.brain.memory import MemoryStore
from tokenpal.brain.proactive import ProactiveScheduler
from tokenpal.brain.schedule import Schedule

_BASE = datetime(2026, 3, 10, 12, 0, 0).timestamp()


def _make(
    paused: bool = False, memory: MemoryStore | None = None
) -> ProactiveScheduler:
    return ProactiveScheduler(is_paused=lambda: paused, memory=memory)


def _tick(sched: ProactiveScheduler, now: float) -> list[str]:
    """The labels `tick()` fired, in the order it returned them."""
    return [n.label for n in sched.tick(now=now)]


def _interval(minutes: float) -> Schedule:
    return Schedule(kind="interval", interval_s=minutes * 60.0)


@pytest.fixture()
def memory(tmp_path: Path) -> Iterator[MemoryStore]:
    store = MemoryStore(db_path=tmp_path / "brain.db")
    store.setup()
    yield store
    store.teardown()


def test_fires_after_interval_and_rearms_from_now() -> None:
    sched = _make()
    sched.register(
        id="stretch", label="stretch!", schedule=_interval(10), next_due_at=_BASE + 600
    )

    assert _tick(sched, _BASE) == []
    assert _tick(sched, _BASE + 599.0) == []

    # Fires 30 s late; the next deadline is 10 min from NOW, not from the
    # deadline it missed.
    assert _tick(sched, _BASE + 630.0) == ["stretch!"]
    assert sched.armed()[0].next_due_at == _BASE + 630.0 + 600
    assert sched.armed()[0].last_fired_at == _BASE + 630.0

    assert _tick(sched, _BASE + 1000.0) == []
    assert _tick(sched, _BASE + 1231.0) == ["stretch!"]


def test_five_hour_gap_fires_exactly_once() -> None:
    """The wake-once rule: a closed lid owes one nudge, not a backlog."""
    sched = _make()
    sched.register(
        id="stretch", label="stretch!", schedule=_interval(60), next_due_at=_BASE
    )

    wake = _BASE + 5 * 3600
    assert _tick(sched, wake) == ["stretch!"]
    assert sched.armed()[0].next_due_at == wake + 3600
    assert sched.armed()[0].next_due_at > wake

    # And the very next tick, an instant later, fires nothing more.
    assert _tick(sched, wake + 0.5) == []


def test_paused_tick_does_not_advance_the_deadline() -> None:
    paused = {"v": True}
    sched = ProactiveScheduler(is_paused=lambda: paused["v"])
    sched.register(
        id="water", label="drink", schedule=_interval(5), next_due_at=_BASE + 300
    )

    assert _tick(sched, _BASE + 3000.0) == []
    assert sched.armed()[0].next_due_at == _BASE + 300

    paused["v"] = False
    assert _tick(sched, _BASE + 3001.0) == ["drink"]


def test_daily_fires_at_its_instant_and_rearms_for_tomorrow() -> None:
    sched = _make()
    schedule = Schedule(kind="daily", at_hour=23, at_minute=0)
    tonight = datetime(2026, 3, 10, 23, 0, 0).timestamp()
    sched.register(
        id="bedtime", label="wind down", schedule=schedule, next_due_at=tonight
    )

    assert _tick(sched, tonight - 60.0) == []
    assert _tick(sched, tonight) == ["wind down"]
    assert sched.armed()[0].next_due_at == datetime(2026, 3, 11, 23, 0, 0).timestamp()

    # An hour later it does not fire again — a daily schedule fires once.
    assert _tick(sched, tonight + 3600.0) == []


def test_register_defaults_the_deadline_to_the_next_occurrence() -> None:
    sched = _make()
    sched.register(id="eye", label="blink", schedule=_interval(20))
    nudge = sched.armed()[0]
    assert nudge.last_fired_at is None
    assert 20 * 60 - 5 < nudge.next_due_at - datetime.now().timestamp() <= 20 * 60


def test_cancel_removes() -> None:
    sched = _make()
    sched.register(id="eye", label="blink", schedule=_interval(1), next_due_at=_BASE)
    assert [n.id for n in sched.armed()] == ["eye"]

    assert sched.cancel("eye") is True
    assert sched.armed() == []
    assert _tick(sched, _BASE + 10_000.0) == []

    assert sched.cancel("not-there") is False


def test_armed_reports_registered_nudges_soonest_first() -> None:
    sched = _make()
    sched.register(
        id="late", label="later", schedule=_interval(60), next_due_at=_BASE + 3600
    )
    sched.register(
        id="soon", label="sooner", schedule=_interval(5), next_due_at=_BASE + 300
    )
    assert [n.id for n in sched.armed()] == ["soon", "late"]
    assert [n.label for n in sched.armed()] == ["sooner", "later"]


def test_tick_delivers_nothing_and_hands_the_fire_to_its_caller() -> None:
    """The pure-clock contract. The scheduler holds no delivery sink at all:
    delivery is off-loop and must produce exactly one bubble per fire, which
    it cannot if tick() also speaks the canned label.
    """
    sched = _make()
    sched.register(
        id="first", label="boom", schedule=_interval(5), next_due_at=_BASE
    )
    sched.register(
        id="second", label="fine", schedule=_interval(5), next_due_at=_BASE + 1
    )

    assert not hasattr(sched, "_ui_callback")

    first = sched.tick(now=_BASE + 10.0)
    assert [(n.id, n.label) for n in first] == [("first", "boom")]

    second = sched.tick(now=_BASE + 40.0)
    assert [(n.id, n.label) for n in second] == [("second", "fine")]


def test_only_one_nudge_fires_per_tick() -> None:
    """show_speech replaces the bubble, so a pile-up would show only the last."""
    sched = _make()
    for name in ("stretch", "water", "eyes"):
        sched.register(
            id=name, label=name, schedule=_interval(60), next_due_at=_BASE
        )

    assert [n.id for n in sched.tick(now=_BASE + 5.0)] == ["stretch"]

    # The brain ticks every 2 s but the bubble lingers 15 s, so the next two
    # wait rather than overwriting the one the user is still reading.
    assert sched.tick(now=_BASE + 7.0) == []
    assert sched.tick(now=_BASE + 9.0) == []

    assert [n.id for n in sched.tick(now=_BASE + 25.0)] == ["water"]
    assert [n.id for n in sched.tick(now=_BASE + 45.0)] == ["eyes"]
    # Each re-armed an hour out from the tick that actually fired it.
    assert sorted(n.next_due_at for n in sched.armed()) == [
        _BASE + 5.0 + 3600, _BASE + 25.0 + 3600, _BASE + 45.0 + 3600
    ]


def test_wake_once_survives_the_full_restart_path(memory: MemoryStore) -> None:
    """Arm, fire, quit, relaunch, tick past a five-hour gap: one fire."""
    sched = _make(memory=memory)
    sched.register(
        id="stretch", label="stretch!", schedule=_interval(60), next_due_at=_BASE
    )
    sched.tick(now=_BASE)

    revived = _make(memory=memory)
    assert revived.hydrate() == 1
    wake = _BASE + 5 * 3600
    assert _tick(revived, wake) == ["stretch!"]
    assert revived.tick(now=wake + 1.0) == []

    row = memory.list_reminders()[0]
    assert row["next_due_at"] == wake + 3600
    assert row["next_due_at"] > wake
    assert row["last_fired_at"] == wake


def test_hydrate_repairs_a_deadline_no_schedule_could_produce(
    memory: MemoryStore,
) -> None:
    """A clock that jumped forward and was corrected would silence it forever."""
    sched = _make(memory=memory)
    sched.register(
        id="stretch",
        label="stretch!",
        schedule=_interval(60),
        next_due_at=_BASE + 10 * 365 * 86400,
    )

    revived = _make(memory=memory)
    revived.hydrate()
    repaired = revived.armed()[0].next_due_at
    assert time.time() < repaired < time.time() + 26 * 3600
    # Repaired on disk too, or every launch re-clamps and a short session
    # never reaches the fire.
    assert memory.list_reminders()[0]["next_due_at"] == repaired


def test_tick_does_not_relist_reminders(memory: MemoryStore) -> None:
    """Hydrate once at start, never per tick.

    tick() does still take MemoryStore's lock for its write-through on a
    fire; what it must not do is re-read the whole table every 2 s.
    """
    sched = _make(memory=memory)
    sched.register(
        id="stretch", label="stretch!", schedule=_interval(60), next_due_at=_BASE
    )
    sched.hydrate()

    reads = 0
    original = memory.list_reminders

    def counting() -> list[dict[str, object]]:
        nonlocal reads
        reads += 1
        return original()

    memory.list_reminders = counting  # type: ignore[method-assign]
    try:
        for i in range(5):
            sched.tick(now=_BASE + 3600.0 * (i + 1))
        assert sched.armed()[0].next_due_at > _BASE
    finally:
        memory.list_reminders = original  # type: ignore[method-assign]
    assert reads == 0


def test_persisted_reminder_survives_a_restart(memory: MemoryStore) -> None:
    sched = _make(memory=memory)
    sched.register(
        id="stretch", label="stretch!", schedule=_interval(60), next_due_at=_BASE
    )
    assert _tick(sched, _BASE + 5.0) == ["stretch!"]

    revived = _make(memory=memory)
    assert revived.hydrate() == 1
    nudge = revived.armed()[0]
    row = memory.list_reminders()[0]

    assert nudge.id == "stretch"
    assert nudge.label == "stretch!"
    assert nudge.schedule == _interval(60)
    assert nudge.last_fired_at == row["last_fired_at"] == _BASE + 5.0
    assert nudge.next_due_at == row["next_due_at"] == _BASE + 5.0 + 3600


def test_rearming_preserves_last_fired_at(memory: MemoryStore) -> None:
    sched = _make(memory=memory)
    sched.register(
        id="stretch", label="stretch!", schedule=_interval(60), next_due_at=_BASE
    )
    sched.tick(now=_BASE + 5.0)

    sched.register(
        id="stretch",
        label="new text",
        schedule=_interval(30),
        next_due_at=_BASE + 9999,
    )
    nudge = sched.armed()[0]
    row = memory.list_reminders()[0]

    assert nudge.last_fired_at == row["last_fired_at"] == _BASE + 5.0
    assert nudge.label == row["label"] == "new text"
    assert nudge.next_due_at == row["next_due_at"] == _BASE + 9999


def test_cancel_unpersists(memory: MemoryStore) -> None:
    sched = _make(memory=memory)
    sched.register(id="stretch", label="stretch!", schedule=_interval(60))
    assert sched.cancel("stretch") is True
    assert memory.list_reminders() == []


def test_disarmed_row_drops_the_in_memory_nudge(memory: MemoryStore) -> None:
    """Disarmed from chat between the due-check and the write-through."""
    sched = _make(memory=memory)
    sched.register(
        id="stretch", label="stretch!", schedule=_interval(60), next_due_at=_BASE
    )

    memory.delete_reminder("stretch")

    assert _tick(sched, _BASE + 5.0) == []
    assert sched.armed() == []


def test_hydrate_skips_an_unreadable_row(memory: MemoryStore) -> None:
    sched = _make(memory=memory)
    sched.register(id="good", label="fine", schedule=_interval(60))
    # A hand-edited or future-version database. upsert_reminder validates, so
    # this can only be reached by writing behind it.
    with memory._lock:
        memory._conn.execute(  # type: ignore[union-attr]
            "INSERT INTO reminders (id, label, kind, armed_at, next_due_at) "
            "VALUES ('bad', 'x', 'lunar', 0, 0)"
        )
        memory._conn.commit()  # type: ignore[union-attr]

    revived = _make(memory=memory)
    assert revived.hydrate() == 1
    assert [n.id for n in revived.armed()] == ["good"]


def test_disabled_memory_stays_in_memory_only(tmp_path: Path) -> None:
    """A disabled store answers every write with a no-op; nudges still fire."""
    store = MemoryStore(db_path=tmp_path / "brain.db", enabled=False)
    sched = _make(memory=store)
    sched.register(id="stretch", label="stretch!", schedule=_interval(60),
                   next_due_at=_BASE)
    assert _tick(sched, _BASE + 5.0) == ["stretch!"]
