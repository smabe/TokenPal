"""System tray / menu-bar icon with a stub right-click menu.

Phase 2: Show/Hide buddy · Quit. Voice ▸ / Mood ▸ / Options / Pause
all land in Phase 4 (parity pass) once they have real data to drive them.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QAction, QIcon, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from tokenpal.ui.palette import BUDDY_GREEN

_HIDE_LABEL = "Hide buddy"
_SHOW_LABEL = "Show buddy"


def _fallback_icon() -> QIcon:
    """Until we wire real voice art through, use a solid-color 32×32
    pixmap so the tray doesn't render blank on hosts without a themed
    icon set."""
    pix = QPixmap(32, 32)
    pix.fill(BUDDY_GREEN)
    return QIcon(pix)


class BuddyTrayIcon(QSystemTrayIcon):
    def __init__(
        self,
        on_toggle_buddy: Callable[[], None],
        on_quit: Callable[[], None],
        parent: QWidget | None = None,
        icon: QIcon | None = None,
    ) -> None:
        super().__init__(icon or _fallback_icon(), parent)
        self.setToolTip("TokenPal")

        menu = QMenu()

        self._toggle_action = QAction(_HIDE_LABEL, menu)
        self._toggle_action.triggered.connect(on_toggle_buddy)
        menu.addAction(self._toggle_action)

        menu.addSeparator()

        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(on_quit)
        menu.addAction(quit_action)

        self.setContextMenu(menu)
        # Retain a reference or Qt will garbage-collect the menu and the
        # tray click will pop up nothing on macOS.
        self._menu = menu

    def set_buddy_visible(self, visible: bool) -> None:
        self._toggle_action.setText(_HIDE_LABEL if visible else _SHOW_LABEL)
