"""Tests for the typing_cadence sense."""

from __future__ import annotations

import re
import time
from pathlib import Path

from tokenpal.senses.typing_cadence.sense import (
    _HYSTERESIS_POLLS,
    _WINDOW_S,
    TypingCadence,
)


def _make() -> TypingCadence:
    return TypingCadence({})


def _refill(sense: TypingCadence, now: float, count: int) -> None:
    """Fill the rolling window with *count* synthetic presses ending at *now*."""
    sense._presses.clear()
    spacing = _WINDOW_S / max(count, 1)
    for i in range(count):
        ts = now - _WINDOW_S + spacing * i
        sense._presses.append(ts)
        sense._last_press = ts


async def _poll_at(sense: TypingCadence, monkeypatch, now: float):
    monkeypatch.setattr(time, "monotonic", lambda: now)
    return await sense.poll()


async def test_idle_with_no_presses() -> None:
    sense = _make()
    assert await sense.poll() is None
    assert sense._current_bucket == "idle"


async def test_transition_requires_hysteresis(monkeypatch) -> None:
    """A single poll in a new bucket must NOT transition — hysteresis kicks in."""
    sense = _make()
    now = 1000.0
    # ~40 WPM → normal
    _refill(sense, now, 100)
    first = await _poll_at(sense, monkeypatch, now)
    assert first is None
    assert sense._current_bucket == "idle"

    # Second poll at same rate commits the transition.
    _refill(sense, now + 2, 100)
    second = await _poll_at(sense, monkeypatch, now + 2)
    assert second is not None
    assert second.data["bucket"] == "normal"
    assert second.changed_from == "idle"


async def test_oscillation_does_not_transition(monkeypatch) -> None:
    """Alternating between two buckets must never cross the hysteresis threshold."""
    sense = _make()
    sense._current_bucket = "normal"
    sense._pending_bucket = "normal"
    now = 1000.0
    # Alternate slow / normal every poll. Neither should commit.
    for i in range(10):
        rate = 40 if i % 2 == 0 else 140  # normal vs slow (WPM ≈ 16)
        _refill(sense, now + 2 * i, rate)
        reading = await _poll_at(sense, monkeypatch, now + 2 * i)
        assert reading is None, f"oscillation poll {i} wrongly transitioned"
    assert sense._current_bucket == "normal"


async def test_rapid_transition_reports_wpm(monkeypatch) -> None:
    sense = _make()
    now = 1000.0
    # ~64 WPM sustained → rapid after hysteresis
    for i in range(_HYSTERESIS_POLLS):
        _refill(sense, now + 2 * i, 160)
        reading = await _poll_at(sense, monkeypatch, now + 2 * i)
    assert reading is not None
    assert reading.data["bucket"] == "rapid"
    assert "WPM" in reading.summary


async def test_steady_state_no_reading(monkeypatch) -> None:
    sense = _make()
    sense._current_bucket = "slow"
    sense._pending_bucket = "slow"
    now = 1000.0
    _refill(sense, now, 40)  # ~16 WPM = slow
    assert await _poll_at(sense, monkeypatch, now) is None


async def test_sustained_burst_fires_once(monkeypatch) -> None:
    sense = _make()
    now = 1000.0

    # Enter rapid after hysteresis.
    for i in range(_HYSTERESIS_POLLS):
        _refill(sense, now + 2 * i, 160)
        first = await _poll_at(sense, monkeypatch, now + 2 * i)
    assert first is not None
    assert first.data["event"] == "bucket_change"

    # 11 min later, still rapid.
    later = now + 11 * 60
    _refill(sense, later, 160)
    second = await _poll_at(sense, monkeypatch, later)
    assert second is not None
    assert second.data["event"] == "sustained_burst"
    assert second.data["minutes"] >= 10

    # Next poll must NOT re-emit the sustained-burst reading.
    even_later = later + 5
    _refill(sense, even_later, 160)
    third = await _poll_at(sense, monkeypatch, even_later)
    assert third is None


async def test_post_burst_silence(monkeypatch) -> None:
    sense = _make()
    now = 1000.0
    # Enter rapid.
    for i in range(_HYSTERESIS_POLLS):
        _refill(sense, now + 2 * i, 160)
        await _poll_at(sense, monkeypatch, now + 2 * i)
    assert sense._current_bucket == "rapid"

    # Jump past window + silence threshold with no presses.
    silent = now + _WINDOW_S + 9
    # Also needs hysteresis to transition to idle.
    first_silent = await _poll_at(sense, monkeypatch, silent)
    # First poll starts the hysteresis but doesn't transition yet, so no reading.
    assert first_silent is None
    reading = await _poll_at(sense, monkeypatch, silent + 2)
    assert reading is not None
    assert reading.data["event"] == "post_burst_silence"


def test_privacy_no_key_value_access() -> None:
    """Guardrail: the sense must never touch key-value attributes on events."""
    src = Path(__file__).resolve().parents[2] / "tokenpal/senses/typing_cadence/sense.py"
    text = src.read_text()
    forbidden = [r"key\.char", r"key\.name", r"key\.vk"]
    for pattern in forbidden:
        assert not re.search(pattern, text), f"typing_cadence must not read {pattern!r}"


def test_privacy_no_pynput_import() -> None:
    """The sense must NOT import pynput directly — it goes through the bus."""
    src = Path(__file__).resolve().parents[2] / "tokenpal/senses/typing_cadence/sense.py"
    text = src.read_text()
    assert not re.search(r"^\s*(?:from\s+pynput|import\s+pynput)", text, re.MULTILINE), (
        "typing_cadence must only touch the keyboard bus, not pynput"
    )
