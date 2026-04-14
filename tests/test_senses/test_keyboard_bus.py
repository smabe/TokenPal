"""Tests for the shared keyboard event bus."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tokenpal.senses import _keyboard_bus


@pytest.fixture(autouse=True)
def _fake_listener():
    """Stop the bus from spawning a real pynput listener during tests.

    On macOS, pynput's listener needs Accessibility permission and will
    abort the process without it. We stub pynput.keyboard.Listener for
    the duration of every test in this module.
    """
    fake_listener = MagicMock()
    fake_listener.start = MagicMock()
    fake_listener.stop = MagicMock()

    fake_keyboard = MagicMock()
    fake_keyboard.Listener = MagicMock(return_value=fake_listener)

    fake_pynput = MagicMock()
    fake_pynput.keyboard = fake_keyboard

    with patch.dict("sys.modules", {"pynput": fake_pynput, "pynput.keyboard": fake_keyboard}):
        # Reset module state between tests.
        _keyboard_bus._subscribers.clear()
        _keyboard_bus._listener = None
        yield
        _keyboard_bus._subscribers.clear()
        _keyboard_bus._listener = None


def test_subscribe_dispatches_to_all() -> None:
    hits_a: list[int] = []
    hits_b: list[int] = []

    _keyboard_bus.subscribe(lambda: hits_a.append(1))
    _keyboard_bus.subscribe(lambda: hits_b.append(1))
    _keyboard_bus._dispatch(None)
    _keyboard_bus._dispatch(None)

    assert hits_a == [1, 1]
    assert hits_b == [1, 1]


def test_unsubscribe_stops_delivery() -> None:
    hits: list[int] = []

    def cb() -> None:
        hits.append(1)

    _keyboard_bus.subscribe(cb)
    _keyboard_bus._dispatch(None)
    _keyboard_bus.unsubscribe(cb)
    _keyboard_bus._dispatch(None)

    assert hits == [1]


def test_subscriber_exception_does_not_break_bus() -> None:
    other_hits: list[int] = []

    def raises() -> None:
        raise RuntimeError("boom")

    _keyboard_bus.subscribe(raises)
    _keyboard_bus.subscribe(lambda: other_hits.append(1))
    _keyboard_bus._dispatch(None)

    assert other_hits == [1]


def test_listener_stops_when_last_subscriber_leaves() -> None:
    def a() -> None:
        pass

    def b() -> None:
        pass

    _keyboard_bus.subscribe(a)
    _keyboard_bus.subscribe(b)
    first_listener = _keyboard_bus._listener
    assert first_listener is not None

    _keyboard_bus.unsubscribe(a)
    assert _keyboard_bus._listener is first_listener  # still up, b subscribed

    _keyboard_bus.unsubscribe(b)
    assert _keyboard_bus._listener is None
    first_listener.stop.assert_called_once()


def test_unsubscribe_unknown_callback_is_noop() -> None:
    _keyboard_bus.unsubscribe(lambda: None)
