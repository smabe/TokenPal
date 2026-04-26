"""Platform-specific polish for the Qt frontend.

- apply_macos_accessory_mode() is a no-op off macOS and silent on macOS
  without pyobjc.
- warn_wayland_limitations() logs once on a Wayland session, quiet
  elsewhere.
- BuddyTrayIcon routes left-click / double-click activation reasons
  through the toggle callback.
"""

from __future__ import annotations

import logging

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QSystemTrayIcon  # noqa: E402

from tokenpal.ui.qt.platform import (  # noqa: E402
    apply_macos_accessory_mode,
    warn_wayland_limitations,
)
from tokenpal.ui.qt.tray import BuddyTrayIcon  # noqa: E402


@pytest.fixture
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


# --- macOS accessory mode -----------------------------------------------


def test_accessory_mode_is_noop_off_macos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tokenpal.ui.qt.platform.sys.platform", "linux")
    # Should not raise or try to import AppKit.
    apply_macos_accessory_mode()


def test_accessory_mode_handles_missing_pyobjc(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """On macOS hosts where the user didn't install the macos extra,
    we should log at DEBUG and keep going — not crash the UI boot."""
    monkeypatch.setattr("tokenpal.ui.qt.platform.sys.platform", "darwin")
    import builtins
    orig_import = builtins.__import__

    def _fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "AppKit":
            raise ImportError("AppKit not available")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    with caplog.at_level(logging.DEBUG, logger="tokenpal.ui.qt.platform"):
        apply_macos_accessory_mode()
    assert any("pyobjc" in rec.message for rec in caplog.records)


def test_accessory_mode_calls_setactivationpolicy_on_macos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: on macOS with pyobjc installed, the function must
    actually call setActivationPolicy_ with the accessory constant.
    Mocks AppKit to avoid depending on the live Cocoa state."""
    monkeypatch.setattr("tokenpal.ui.qt.platform.sys.platform", "darwin")

    calls: list[int] = []

    class _FakeApp:
        def setActivationPolicy_(self, policy: int) -> None:
            calls.append(policy)

    class _FakeNSApplication:
        @staticmethod
        def sharedApplication() -> _FakeApp:
            return _FakeApp()

    fake_module = type(
        "appkit",
        (),
        {
            "NSApplication": _FakeNSApplication,
            "NSApplicationActivationPolicyAccessory": 1,
        },
    )
    monkeypatch.setitem(__import__("sys").modules, "AppKit", fake_module)
    apply_macos_accessory_mode()
    assert calls == [1]


def test_accessory_mode_logs_and_returns_on_cocoa_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If setActivationPolicy_ raises (pyobjc bridge flake, API rename
    on a new macOS), the function must log and return — not crash the
    UI boot."""
    monkeypatch.setattr("tokenpal.ui.qt.platform.sys.platform", "darwin")

    class _ExplodingApp:
        def setActivationPolicy_(self, _policy: int) -> None:
            raise RuntimeError("pyobjc bridge unhappy")

    class _FakeNSApplication:
        @staticmethod
        def sharedApplication() -> _ExplodingApp:
            return _ExplodingApp()

    fake_module = type(
        "appkit",
        (),
        {
            "NSApplication": _FakeNSApplication,
            "NSApplicationActivationPolicyAccessory": 1,
        },
    )
    monkeypatch.setitem(__import__("sys").modules, "AppKit", fake_module)
    with caplog.at_level(logging.ERROR, logger="tokenpal.ui.qt.platform"):
        apply_macos_accessory_mode()
    assert any("accessory" in rec.message.lower() for rec in caplog.records)


# --- Wayland warning ----------------------------------------------------


def test_wayland_warning_fires_on_wayland_session(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr("tokenpal.ui.qt.platform.sys.platform", "linux")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    with caplog.at_level(logging.INFO, logger="tokenpal.ui.qt.platform"):
        warn_wayland_limitations()
    assert any("Wayland" in rec.message for rec in caplog.records)


def test_wayland_warning_silent_on_x11(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr("tokenpal.ui.qt.platform.sys.platform", "linux")
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    with caplog.at_level(logging.INFO, logger="tokenpal.ui.qt.platform"):
        warn_wayland_limitations()
    assert not any("Wayland" in rec.message for rec in caplog.records)


def test_wayland_warning_silent_off_linux(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr("tokenpal.ui.qt.platform.sys.platform", "darwin")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    with caplog.at_level(logging.INFO, logger="tokenpal.ui.qt.platform"):
        warn_wayland_limitations()
    assert not any("Wayland" in rec.message for rec in caplog.records)


# --- Tray activation ----------------------------------------------------


def test_tray_click_does_not_toggle_buddy(qapp: QApplication) -> None:
    """Clicking the tray icon should only pop the menu — no direct
    toggle. The user explicitly picks Show/Hide from the menu."""
    calls: list[int] = []
    tray = BuddyTrayIcon(
        on_toggle_buddy=lambda: calls.append(1),
        on_toggle_chat=lambda: None,
        on_toggle_news=lambda: None,
        on_options=lambda: None,
        on_quit=lambda: None,
    )
    # No `activated` signal consumer any more — emitting Trigger /
    # DoubleClick on the tray must not invoke on_toggle_buddy.
    tray.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
    tray.activated.emit(QSystemTrayIcon.ActivationReason.DoubleClick)
    tray.activated.emit(QSystemTrayIcon.ActivationReason.Context)
    assert calls == []


def test_tray_chat_toggle_label_flips_with_visibility(
    qapp: QApplication,
) -> None:
    """The 'Show chat' / 'Hide chat' menu label must track whatever the
    overlay reports, otherwise the menu lies about the current state."""
    tray = BuddyTrayIcon(
        on_toggle_buddy=lambda: None,
        on_toggle_chat=lambda: None,
        on_toggle_news=lambda: None,
        on_options=lambda: None,
        on_quit=lambda: None,
    )
    tray.set_chat_visible(True)
    assert tray._toggle_chat_action.text() == "Hide chat log"
    tray.set_chat_visible(False)
    assert tray._toggle_chat_action.text() == "Show chat log"


