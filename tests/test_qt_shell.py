"""Smoke tests for the Qt shell.

Boots a real QApplication, constructs the buddy + tray, runs the event
loop for a single tick, and quits. Proves the ~wiring imports and the
widgets construct without exceptions on this host. Deeper interaction
tests land once the brain adapter is wired (Phase 3).

Skipped when PySide6 isn't installed — the shell is opt-in via the
``tokenpal[desktop]`` extra.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tokenpal.ui.qt.app import build_shell  # noqa: E402


@pytest.fixture
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    return app  # type: ignore[return-value]


def test_shell_constructs_and_quits(qapp: QApplication) -> None:
    shell = build_shell(app=qapp)
    shell.buddy.show()
    # Don't call tray.show() — system tray availability varies on CI
    # hosts, and the buddy window is enough to prove the shell boots.
    QTimer.singleShot(50, qapp.quit)
    qapp.exec()
    assert shell.buddy is not None
    assert shell.tray is not None
    shell.buddy.close()


def test_buddy_window_has_frameless_flags(qapp: QApplication) -> None:
    from PySide6.QtCore import Qt
    shell = build_shell(app=qapp)
    flags = shell.buddy.windowFlags()
    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    shell.buddy.close()


def test_tray_menu_has_toggle_and_quit(qapp: QApplication) -> None:
    shell = build_shell(app=qapp)
    menu = shell.tray.contextMenu()
    assert menu is not None
    labels = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert "Hide buddy" in labels or "Show buddy" in labels
    assert "Quit" in labels
    shell.buddy.close()
