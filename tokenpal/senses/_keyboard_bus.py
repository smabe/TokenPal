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
import platform
import threading
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)


def _patch_pynput_darwin_tsm() -> None:
    """Work around macOS 26+ crash in pynput's keycode_context.

    pynput's Listener enters ``keycode_context()`` on its listener thread,
    which calls ``TISCopyCurrentKeyboardInputSource`` → ``TSMGetInputSourceProperty``.
    Starting with macOS Tahoe, those Text Services APIs enforce main-thread
    via ``dispatch_assert_queue`` and SIGTRAP if violated.

    The captured layout data is only used by ``keycode_to_string`` — which
    the Quartz event path never touches (see ``_event_to_key`` in
    pynput/keyboard/_darwin.py; it uses ``CGEventKeyboardGetUnicodeString``
    instead). Yielding a no-op tuple sidesteps the crash without breaking
    key translation.
    """
    if platform.system() != "Darwin":
        return
    try:
        from contextlib import contextmanager

        from pynput._util import darwin as _pynput_darwin
        from pynput.keyboard import _darwin as _pynput_kbd_darwin
    except ImportError:
        return
    if getattr(_pynput_darwin.keycode_context, "_tokenpal_patched", False):
        return

    @contextmanager
    def _safe_keycode_context() -> Any:
        yield (None, None)

    _safe_keycode_context._tokenpal_patched = True  # type: ignore[attr-defined]
    _pynput_darwin.keycode_context = _safe_keycode_context
    # keyboard._darwin imports keycode_context by value — patch the rebound name
    _pynput_kbd_darwin.keycode_context = _safe_keycode_context

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
            _patch_pynput_darwin_tsm()
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
