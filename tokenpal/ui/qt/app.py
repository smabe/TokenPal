"""Qt shell entry point — QApplication boot, window + tray wiring.

Phase 2 scope: construct the app, show a static-frame buddy, pop a
tray icon with Show/Hide + Quit. No brain wiring.

The adapter port (``QtOverlay`` implementing ``AbstractOverlay``) lands
in Phase 3.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from tokenpal.ui.ascii_renderer import BUDDY_IDLE
from tokenpal.ui.qt import ensure_qapplication
from tokenpal.ui.qt.buddy_window import BuddyWindow
from tokenpal.ui.qt.tray import BuddyTrayIcon


@dataclass
class QtShell:
    app: QApplication
    buddy: BuddyWindow
    tray: BuddyTrayIcon


def build_shell(app: QApplication | None = None) -> QtShell:
    """Wire the buddy + tray to an existing (or new) QApplication.

    Returns the shell so callers (tests, entry points) can attach
    additional signals before running the event loop.
    """
    qapp = ensure_qapplication(app)
    assert isinstance(qapp, QApplication)
    qapp.setQuitOnLastWindowClosed(False)

    buddy = BuddyWindow(
        frame_lines=BUDDY_IDLE,
        initial_anchor=(400.0, 200.0),
    )

    def _toggle_buddy() -> None:
        if buddy.isVisible():
            buddy.hide()
            tray.set_buddy_visible(False)
        else:
            buddy.show()
            tray.set_buddy_visible(True)

    def _quit() -> None:
        qapp.quit()

    # Pre-brain shell has no chat window or options dispatcher; make
    # those actions harmless no-ops so the menu still renders.
    tray = BuddyTrayIcon(
        on_toggle_buddy=_toggle_buddy,
        on_toggle_chat=lambda: None,
        on_toggle_news=lambda: None,
        on_options=lambda: None,
        on_quit=_quit,
    )
    buddy.set_right_click_handler(
        lambda global_pos: _popup_tray_menu(tray, global_pos),
    )

    return QtShell(app=qapp, buddy=buddy, tray=tray)


def _popup_tray_menu(tray: BuddyTrayIcon, global_pos: QPoint) -> None:
    menu = tray.contextMenu()
    if menu is not None:
        menu.popup(global_pos)


def run() -> int:
    """Standalone entry point — useful for manual smoke-testing before
    the adapter lands. Will be replaced by the ``tokenpal`` CLI picking
    Qt via the ``[ui] overlay = "qt"`` config in Phase 6."""
    shell = build_shell()
    shell.buddy.show()
    shell.tray.show()
    return shell.app.exec()


if __name__ == "__main__":
    sys.exit(run())
