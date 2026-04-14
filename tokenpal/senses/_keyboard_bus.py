"""Shared keyboard-event bus.

Single pynput keyboard listener fans events out to any sense that
subscribes. Prevents double-listener overhead when multiple senses
(idle, typing_cadence, future phase-D subscribers) need the same signal.

Subscribers receive a bare "a key was pressed" notification — no key
value, no modifiers, nothing that could leak what the user typed.
Subscribers are called on the pynput listener thread, so they must be
quick and thread-safe (typically just a counter bump or timestamp).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

Subscriber = Callable[[], None]

_lock = threading.Lock()
_subscribers: list[Subscriber] = []
_listener: Any = None


def subscribe(callback: Subscriber) -> None:
    """Register *callback* to fire once per keypress.

    Starts the underlying pynput listener on the first subscription.
    No-op if pynput is not installed — callers should degrade silently.
    """
    global _listener
    with _lock:
        _subscribers.append(callback)
        if _listener is not None:
            return

        try:
            from pynput import keyboard
        except ImportError:
            log.warning("pynput not installed — keyboard bus inactive")
            return

        _listener = keyboard.Listener(on_press=_dispatch)
        _listener.start()
        log.info("Keyboard bus: pynput listener started")


def unsubscribe(callback: Subscriber) -> None:
    """Remove *callback*. Stops the listener when the last subscriber leaves."""
    global _listener
    with _lock:
        try:
            _subscribers.remove(callback)
        except ValueError:
            return
        if _subscribers or _listener is None:
            return
        _listener.stop()
        _listener = None
        log.info("Keyboard bus: pynput listener stopped")


def _dispatch(_key: Any) -> None:
    """Fan out a keypress to every subscriber. Called on the pynput thread.

    _key is discarded at the module boundary so subscribers can never see
    what was typed, even by accident.
    """
    with _lock:
        snapshot = list(_subscribers)
    for cb in snapshot:
        try:
            cb()
        except Exception:
            log.exception("Keyboard bus subscriber raised")
