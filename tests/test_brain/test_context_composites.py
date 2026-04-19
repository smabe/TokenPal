"""Tests for ContextWindowBuilder composites — focus on the AFK composite
and the snapshot suppression contract."""

from __future__ import annotations

import time

from tokenpal.brain.context import ContextWindowBuilder
from tokenpal.senses.base import SenseReading


def _reading(
    sense_name: str,
    summary: str,
    *,
    data: dict | None = None,
    age_s: float = 0.0,
    confidence: float = 1.0,
) -> SenseReading:
    return SenseReading(
        sense_name=sense_name,
        timestamp=time.monotonic() - age_s,
        data=data or {},
        summary=summary,
        confidence=confidence,
    )


def _builder_with(*readings: SenseReading) -> ContextWindowBuilder:
    builder = ContextWindowBuilder()
    builder.ingest(list(readings))
    return builder


def _afk_setup() -> ContextWindowBuilder:
    """Builder with sustained-idle, an unchanged app, and silent typing."""
    builder = _builder_with(
        _reading(
            "idle",
            "User has been idle for 6 minutes",
            data={"event": "sustained", "idle_minutes": 6, "tier": "medium"},
            confidence=0.5,
        ),
        _reading("app_awareness", "Ghostty"),
        _reading(
            "typing_cadence",
            "User has not been typing",
            data={"bucket": "idle", "wpm": 0},
            confidence=0.4,
        ),
    )
    # Mark the app as already-acknowledged so prev_summary == current summary.
    builder.acknowledge()
    return builder


def test_afk_composite_fires_when_idle_and_app_unchanged() -> None:
    builder = _afk_setup()
    composites = builder._detect_composites()

    assert len(composites) >= 1
    line, suppressed = composites[0]
    assert "Ghostty" in line
    assert "no input" in line.lower() or "idle" in line.lower()
    assert "idle" in suppressed


def test_afk_composite_suppresses_raw_idle_in_snapshot() -> None:
    builder = _afk_setup()
    snap = builder.snapshot()

    # Raw idle line ("User has been idle for 6 minutes") must NOT appear.
    assert "User has been idle for 6 minutes" not in snap
    # AFK composite line must appear.
    assert "Ghostty" in snap
    assert "no input" in snap.lower() or "parked" in snap.lower()


def test_afk_does_not_fire_when_app_changed() -> None:
    builder = _builder_with(
        _reading(
            "idle",
            "User has been idle for 4 minutes",
            data={"event": "sustained", "idle_minutes": 4, "tier": "short"},
        ),
        _reading("app_awareness", "Ghostty"),
    )
    # acknowledge with a DIFFERENT prior app -> prev_summary != current
    builder._prev_summaries["app_awareness"] = "Safari"

    composites = builder._detect_composites()
    assert all("parked" not in line.lower() for line, _ in composites)


def test_afk_does_not_fire_when_user_is_typing() -> None:
    builder = _builder_with(
        _reading(
            "idle",
            "User has been idle for 6 minutes",
            data={"event": "sustained", "idle_minutes": 6, "tier": "medium"},
        ),
        _reading("app_awareness", "Ghostty"),
        _reading(
            "typing_cadence",
            "User typing rapidly",
            data={"bucket": "rapid", "wpm": 80},
        ),
    )
    builder.acknowledge()

    composites = builder._detect_composites()
    assert all("parked" not in line.lower() for line, _ in composites)


def test_afk_does_not_fire_without_sustained_event() -> None:
    """A return-from-idle reading must NOT trigger the AFK composite."""
    builder = _builder_with(
        _reading(
            "idle",
            "User returned after 5 minutes away",
            data={"event": "returned", "tier": "medium"},
        ),
        _reading("app_awareness", "Ghostty"),
    )
    builder.acknowledge()

    composites = builder._detect_composites()
    assert all("parked" not in line.lower() for line, _ in composites)


def test_afk_prepended_so_two_line_cap_keeps_it() -> None:
    """If three composites would fire, AFK survives the cap."""
    builder = _builder_with(
        _reading(
            "idle",
            "User has been idle for 6 minutes",
            data={"event": "sustained", "idle_minutes": 6, "tier": "medium"},
        ),
        _reading("app_awareness", "Ghostty"),
        # High CPU + many switches -> grinding composite
        _reading("hardware", "CPU pinned", data={"cpu_percent": 90}),
        _reading(
            "productivity",
            "Switching a lot",
            data={
                "switches_per_hour": 15,
                "time_in_current_min": 45,
                "session_minutes": 200,
            },
        ),
        # Music playing -> flow composite
        _reading("music", "Track playing", data={"state": "playing"}),
        # Late night -> late-night composite
        _reading("time_awareness", "23:30", data={"hour": 23}),
    )
    builder.acknowledge()

    composites = builder._detect_composites()
    assert len(composites) == 2
    # AFK must be index 0
    assert "parked" in composites[0][0].lower() or "Ghostty" in composites[0][0]


def test_snapshot_skips_suppressed_senses() -> None:
    """Sanity: a composite that suppresses a sense actually skips it."""
    builder = _afk_setup()
    snap = builder.snapshot()
    lines = snap.splitlines()
    # Idle line shouldn't show up; app + typing + composite remain.
    assert not any(line.startswith("User has been idle") for line in lines)
