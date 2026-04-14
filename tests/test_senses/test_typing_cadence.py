"""Tests for the typing_cadence sense."""

from __future__ import annotations

import re
import time
from pathlib import Path

from tokenpal.senses.typing_cadence.sense import TypingCadence, _WINDOW_S


def _make() -> TypingCadence:
    return TypingCadence({})


def _press(sense: TypingCadence, at: float) -> None:
    """Inject a synthetic keypress at *at* (monotonic seconds)."""
    sense._presses.append(at)
    sense._last_press = at


async def test_idle_with_no_presses() -> None:
    sense = _make()
    assert await sense.poll() is None
    assert sense._current_bucket == "idle"


async def test_transition_idle_to_typing(monkeypatch) -> None:
    sense = _make()
    now = 1000.0
    # 20 presses in 15s -> 4 words/s * 60 / 5 = ... ~16 WPM = slow
    for i in range(20):
        _press(sense, now - _WINDOW_S + 0.1 * i)

    monkeypatch.setattr(time, "monotonic", lambda: now)
    reading = await sense.poll()
    assert reading is not None
    assert reading.data["event"] == "bucket_change"
    assert reading.data["bucket"] == "slow"
    assert reading.changed_from == "idle"


async def test_rapid_transition_reports_wpm(monkeypatch) -> None:
    sense = _make()
    now = 1000.0
    # 80 presses in 15s -> ~64 WPM = rapid
    for i in range(80):
        _press(sense, now - _WINDOW_S + 0.1 * i)

    monkeypatch.setattr(time, "monotonic", lambda: now)
    reading = await sense.poll()
    assert reading is not None
    assert reading.data["bucket"] == "rapid"
    assert "WPM" in reading.summary


async def test_steady_state_no_reading(monkeypatch) -> None:
    sense = _make()
    sense._current_bucket = "slow"
    now = 1000.0
    for i in range(20):
        _press(sense, now - _WINDOW_S + 0.1 * i)
    monkeypatch.setattr(time, "monotonic", lambda: now)
    assert await sense.poll() is None


async def test_sustained_burst_fires_once(monkeypatch) -> None:
    sense = _make()
    now = 1000.0

    # Enough presses in-window for rapid (~64 WPM).
    for i in range(80):
        _press(sense, now - _WINDOW_S + 0.1 * i)

    # First poll: transition idle -> rapid.
    monkeypatch.setattr(time, "monotonic", lambda: now)
    first = await sense.poll()
    assert first is not None
    assert first.data["event"] == "bucket_change"

    # 11 min later, still rapid (refill the window).
    later = now + 11 * 60
    for i in range(80):
        _press(sense, later - _WINDOW_S + 0.1 * i)
    monkeypatch.setattr(time, "monotonic", lambda: later)
    second = await sense.poll()
    assert second is not None
    assert second.data["event"] == "sustained_burst"
    assert second.data["minutes"] >= 10

    # Next poll must NOT re-emit the sustained-burst reading.
    even_later = later + 5
    for i in range(80):
        _press(sense, even_later - _WINDOW_S + 0.1 * i)
    monkeypatch.setattr(time, "monotonic", lambda: even_later)
    third = await sense.poll()
    assert third is None


async def test_post_burst_silence(monkeypatch) -> None:
    sense = _make()
    now = 1000.0
    for i in range(80):
        _press(sense, now - _WINDOW_S + 0.1 * i)
    monkeypatch.setattr(time, "monotonic", lambda: now)
    await sense.poll()  # enter rapid

    # Jump forward past the window + silence threshold. No new presses.
    silent = now + _WINDOW_S + 9
    monkeypatch.setattr(time, "monotonic", lambda: silent)
    reading = await sense.poll()
    assert reading is not None
    assert reading.data["event"] == "post_burst_silence"


def test_privacy_no_key_value_access() -> None:
    """Guardrail: the sense must never touch key-value attributes on events."""
    src = Path(__file__).resolve().parents[2] / "tokenpal/senses/typing_cadence/sense.py"
    text = src.read_text()
    forbidden = [r"key\.char", r"key\.name", r"key\.vk", r"\.char\b", r"\.name\b"]
    # `.name` is a legit attribute elsewhere (sense_name); scope the grep to
    # anything referencing a parameter named `key` specifically.
    for pattern in forbidden[:3]:
        assert not re.search(pattern, text), f"typing_cadence must not read {pattern!r}"


def test_privacy_no_pynput_import() -> None:
    """The sense must NOT import pynput directly — it goes through the bus."""
    src = Path(__file__).resolve().parents[2] / "tokenpal/senses/typing_cadence/sense.py"
    text = src.read_text()
    assert not re.search(r"^\s*(?:from\s+pynput|import\s+pynput)", text, re.MULTILINE), (
        "typing_cadence must only touch the keyboard bus, not pynput"
    )
