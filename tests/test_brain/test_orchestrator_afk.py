"""Tests for the AFK-aware behaviors in the Brain orchestrator:
- _sustained_idle_active helper
- _pick_topic weight penalty when sustained-idle is active
"""

from __future__ import annotations

import time
from collections import deque

from tokenpal.brain.context import ContextWindowBuilder
from tokenpal.brain.orchestrator import Brain
from tokenpal.senses.base import SenseReading


def _reading(
    sense_name: str,
    summary: str,
    *,
    data: dict | None = None,
    confidence: float = 1.0,
) -> SenseReading:
    return SenseReading(
        sense_name=sense_name,
        timestamp=time.monotonic(),
        data=data or {},
        summary=summary,
        confidence=confidence,
    )


def _bare_brain(builder: ContextWindowBuilder) -> Brain:
    obj = Brain.__new__(Brain)
    obj._context = builder
    obj._recent_topics = deque(maxlen=10)
    return obj


def test_sustained_idle_active_true_when_event_sustained() -> None:
    builder = ContextWindowBuilder()
    builder.ingest([
        _reading("idle", "User has been idle for 6 minutes",
                 data={"event": "sustained", "tier": "medium"}),
    ])
    brain = _bare_brain(builder)
    assert brain._sustained_idle_active() is True


def test_sustained_idle_active_false_for_returned_event() -> None:
    builder = ContextWindowBuilder()
    builder.ingest([
        _reading("idle", "User returned after 5 minutes away",
                 data={"event": "returned", "tier": "medium"}),
    ])
    brain = _bare_brain(builder)
    assert brain._sustained_idle_active() is False


def test_sustained_idle_active_false_when_idle_absent() -> None:
    builder = ContextWindowBuilder()
    builder.ingest([_reading("app_awareness", "Ghostty")])
    brain = _bare_brain(builder)
    assert brain._sustained_idle_active() is False


def test_pick_topic_demotes_unchanged_app_during_afk() -> None:
    """With sustained-idle + low activity + unchanged app summary, app_awareness
    should lose almost every topic pick."""
    builder = ContextWindowBuilder()
    builder.ingest([
        _reading("idle", "User has been idle for 6 minutes",
                 data={"event": "sustained", "tier": "medium",
                       "idle_minutes": 6}),
        _reading("app_awareness", "Ghostty"),
    ])
    builder.acknowledge()  # prev_summary["app_awareness"] = "Ghostty"
    # Re-ingest idle so its summary is fresh and also acknowledged-as-changed
    # is no longer the case... we want idle to be "fresh" relative to the prev.
    builder.ingest([
        _reading(
            "idle",
            "User has been idle for 7 minutes",
            data={"event": "sustained", "tier": "medium", "idle_minutes": 7},
        ),
    ])

    brain = _bare_brain(builder)
    picks = [brain._pick_topic() for _ in range(200)]
    idle_picks = picks.count("idle")
    app_picks = picks.count("app_awareness")
    # Idle should dominate; app_awareness should be heavily demoted.
    assert idle_picks > app_picks * 3, (
        f"expected idle to dominate during AFK: idle={idle_picks} "
        f"app={app_picks}"
    )


def test_pick_topic_does_not_demote_when_app_changed() -> None:
    """If app_awareness changed (user just switched apps) the demotion shouldn't
    fire — change_bonus should still let it compete normally."""
    builder = ContextWindowBuilder()
    builder.ingest([
        _reading("idle", "User has been idle for 6 minutes",
                 data={"event": "sustained", "tier": "medium",
                       "idle_minutes": 6}),
        _reading("app_awareness", "Ghostty"),
    ])
    # prev_summary["app_awareness"] == "Safari" -> different from current
    builder._prev_summaries["app_awareness"] = "Safari"

    brain = _bare_brain(builder)
    picks = [brain._pick_topic() for _ in range(200)]
    # app_awareness should win a healthy share — change_bonus 1.5x, no demotion.
    assert picks.count("app_awareness") > 30


def test_pick_topic_does_not_demote_when_no_sustained_idle() -> None:
    """If there's no sustained-idle reading at all, the AFK penalty mustn't
    kick in even if activity is low."""
    builder = ContextWindowBuilder()
    builder.ingest([_reading("app_awareness", "Ghostty")])
    builder.acknowledge()  # prev == current, would normally trigger penalty
    builder.ingest([_reading("app_awareness", "Ghostty")])

    brain = _bare_brain(builder)
    # With only app_awareness in available, it must always be picked — no demotion
    # should reduce it to zero.
    picks = [brain._pick_topic() for _ in range(20)]
    assert all(p == "app_awareness" for p in picks)
