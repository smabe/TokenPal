"""Tests for the PynputIdle sense — sustained-idle re-emission and returns."""

from __future__ import annotations

import time

from tokenpal.senses.idle.pynput_idle import (
    _LONG_IDLE,
    _MEDIUM_IDLE,
    _RETURN_DEBOUNCE_SUSTAINED_S,
    _SHORT_IDLE,
    _SUSTAINED_EMIT_INTERVAL_S,
    PynputIdle,
)


def _make() -> PynputIdle:
    sense = PynputIdle({})
    sense._last_input = 1000.0
    return sense


async def _poll_at(sense: PynputIdle, monkeypatch, now: float):
    monkeypatch.setattr(time, "monotonic", lambda: now)
    return await sense.poll()


async def test_active_user_emits_nothing(monkeypatch) -> None:
    sense = _make()
    # Just before the short-idle threshold — still active.
    out = await _poll_at(sense, monkeypatch, sense._last_input + _SHORT_IDLE - 1)
    assert out is None
    assert sense._was_idle is False


async def test_transition_to_idle_is_silent(monkeypatch) -> None:
    """The active->idle transition stays quiet — no reading, no chatter."""
    sense = _make()
    out = await _poll_at(sense, monkeypatch, sense._last_input + _SHORT_IDLE + 1)
    assert out is None
    assert sense._was_idle is True
    # _idle_start tracks the actual moment the user went idle.
    assert sense._idle_start == sense._last_input


async def test_sustained_emits_on_cadence(monkeypatch) -> None:
    """While idle, sustained readings emit roughly every 60s, not every poll."""
    sense = _make()
    base = sense._last_input

    # Slip into idle.
    await _poll_at(sense, monkeypatch, base + _SHORT_IDLE + 1)

    # Polling 5 seconds later should NOT emit — cadence not reached.
    quiet = await _poll_at(sense, monkeypatch, base + _SHORT_IDLE + 6)
    assert quiet is None

    # Polling past the 60s cadence should emit a sustained reading.
    out = await _poll_at(
        sense, monkeypatch, base + _SHORT_IDLE + _SUSTAINED_EMIT_INTERVAL_S + 2
    )
    assert out is not None
    assert out.data["event"] == "sustained"
    assert out.data["tier"] == "short"
    assert "idle" in out.summary.lower()
    assert 0.0 < out.confidence < 1.0


async def test_sustained_emits_on_tier_bump(monkeypatch) -> None:
    """Crossing into a new tier emits immediately even if cadence isn't due."""
    sense = _make()
    base = sense._last_input

    await _poll_at(sense, monkeypatch, base + _SHORT_IDLE + 1)

    # First sustained emission inside short tier.
    short = await _poll_at(
        sense, monkeypatch, base + _SHORT_IDLE + _SUSTAINED_EMIT_INTERVAL_S + 1
    )
    assert short is not None
    assert short.data["tier"] == "short"

    # One second past the medium threshold — tier-bump forces emission
    # regardless of cadence.
    medium = await _poll_at(sense, monkeypatch, base + _MEDIUM_IDLE + 1)
    assert medium is not None
    assert medium.data["tier"] == "medium"
    assert medium.confidence > short.confidence


async def test_return_emits_exactly_one_reading(monkeypatch) -> None:
    """Idle->active still emits the single transition reading after debounce."""
    sense = _make()
    base = sense._last_input

    # Enter and confirm sustained idle.
    await _poll_at(sense, monkeypatch, base + _SHORT_IDLE + 1)
    await _poll_at(
        sense, monkeypatch, base + _SHORT_IDLE + _SUSTAINED_EMIT_INTERVAL_S + 1
    )

    # User returns: first event opens the pending window — no emission yet.
    return_ts = base + _MEDIUM_IDLE + 30
    sense._last_input = return_ts
    sense._event_count += 1
    pending = await _poll_at(sense, monkeypatch, return_ts)
    assert pending is None
    assert sense._pending_return is not None

    # Confirm via burst: a second event before the sustained window expires.
    sense._last_input = return_ts + 1
    sense._event_count += 1
    returned = await _poll_at(sense, monkeypatch, return_ts + 1)
    assert returned is not None
    assert returned.data["event"] == "returned"
    assert "returned" in returned.summary.lower()

    # Next tick after the return must NOT re-emit anything.
    quiet = await _poll_at(sense, monkeypatch, return_ts + 6)
    assert quiet is None


async def test_phantom_event_does_not_trigger_return(monkeypatch) -> None:
    """A single isolated input event after long idle is treated as phantom."""
    sense = _make()
    base = sense._last_input

    # Slip into idle and emit one sustained reading so we know we're parked.
    await _poll_at(sense, monkeypatch, base + _SHORT_IDLE + 1)
    sustained = await _poll_at(
        sense, monkeypatch, base + _SHORT_IDLE + _SUSTAINED_EMIT_INTERVAL_S + 1
    )
    assert sustained is not None and sustained.data["event"] == "sustained"

    # One phantom event lands, then nothing follows.
    phantom_ts = base + _MEDIUM_IDLE + 30
    sense._last_input = phantom_ts
    sense._event_count += 1

    # Poll inside the debounce window — pending opens, no emission.
    pending = await _poll_at(sense, monkeypatch, phantom_ts)
    assert pending is None
    assert sense._pending_return is not None

    # Poll after the debounce window with no follow-up — phantom is discarded
    # and the sense stays in sustained-idle, NOT returning a "returned" reading.
    after = phantom_ts + _RETURN_DEBOUNCE_SUSTAINED_S + 1
    out = await _poll_at(sense, monkeypatch, after)
    assert sense._pending_return is None
    assert sense._was_idle is True
    assert sense._discarded_input_at == phantom_ts
    # Either None (cadence not due) or a sustained reading — never "returned".
    assert out is None or out.data["event"] == "sustained"


async def test_sustained_input_confirms_return(monkeypatch) -> None:
    """Sustained activity (≥5s with at least one event) confirms return."""
    sense = _make()
    base = sense._last_input

    await _poll_at(sense, monkeypatch, base + _SHORT_IDLE + 1)

    # First event opens pending.
    return_ts = base + _MEDIUM_IDLE + 30
    sense._last_input = return_ts
    sense._event_count += 1
    assert await _poll_at(sense, monkeypatch, return_ts) is None

    # Another event a few seconds later, still within sustained window.
    sense._last_input = return_ts + 4
    sense._event_count += 1
    # Poll past the sustained threshold.
    returned = await _poll_at(sense, monkeypatch, return_ts + _RETURN_DEBOUNCE_SUSTAINED_S + 1)
    assert returned is not None
    assert returned.data["event"] == "returned"


async def test_long_tier_summary_uses_hours(monkeypatch) -> None:
    sense = _make()
    base = sense._last_input

    # Skip directly to a long-idle state by setting _last_input far in the past.
    sense._last_input = base
    out = await _poll_at(sense, monkeypatch, base + _LONG_IDLE + 1)
    # First poll past _SHORT_IDLE -> transition (silent).
    assert out is None
    # Wait for the cadence window before the next emission.
    out = await _poll_at(
        sense, monkeypatch, base + _LONG_IDLE + _SUSTAINED_EMIT_INTERVAL_S + 2
    )
    assert out is not None
    assert out.data["tier"] == "long"
    assert out.confidence == 0.7
    assert "hours" in out.summary or "minutes" in out.summary
