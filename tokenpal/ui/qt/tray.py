"""System tray / menu-bar icon with a stub right-click menu.

Phase 2: Show/Hide buddy · Quit. Voice ▸ / Mood ▸ / Options / Pause
all land in Phase 4 (parity pass) once they have real data to drive them.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QAction, QIcon, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from tokenpal.ui.palette import BUDDY_GREEN

_HIDE_BUDDY_LABEL = "Hide buddy"
_SHOW_BUDDY_LABEL = "Show buddy"
_HIDE_CHAT_LABEL = "Hide chat"
_SHOW_CHAT_LABEL = "Show chat"

# Tray activation reasons that should toggle the buddy directly. On
# macOS the menu bar icon only responds via its context menu and a
# single click already pops the menu, so the `Trigger` branch is
# effectively Windows + Linux. Double-click also toggles as a courtesy
# for users who habitually double-click tray icons.
_TOGGLE_ACTIVATION_REASONS = frozenset({
    QSystemTrayIcon.ActivationReason.Trigger,
    QSystemTrayIcon.ActivationReason.DoubleClick,
})


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
        on_toggle_chat: Callable[[], None],
        on_quit: Callable[[], None],
        parent: QWidget | None = None,
        icon: QIcon | None = None,
    ) -> None:
        super().__init__(icon or _fallback_icon(), parent)
        self.setToolTip("TokenPal")
        self._on_toggle_buddy = on_toggle_buddy

        menu = QMenu()

        self._toggle_buddy_action = QAction(_HIDE_BUDDY_LABEL, menu)
        self._toggle_buddy_action.triggered.connect(on_toggle_buddy)
        menu.addAction(self._toggle_buddy_action)

        self._toggle_chat_action = QAction(_HIDE_CHAT_LABEL, menu)
        self._toggle_chat_action.triggered.connect(on_toggle_chat)
        menu.addAction(self._toggle_chat_action)

        menu.addSeparator()

        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(on_quit)
        menu.addAction(quit_action)

        self.setContextMenu(menu)
        # Retain a reference or Qt will garbage-collect the menu and the
        # tray click will pop up nothing on macOS.
        self._menu = menu

        # Left-click on Windows / Linux toggles the buddy window. On
        # macOS the menu bar icon opens the context menu on any click,
        # so this signal never fires there — nothing to special-case.
        self.activated.connect(self._on_activated)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in _TOGGLE_ACTIVATION_REASONS:
            self._on_toggle_buddy()

    def set_buddy_visible(self, visible: bool) -> None:
        self._toggle_buddy_action.setText(
            _HIDE_BUDDY_LABEL if visible else _SHOW_BUDDY_LABEL,
        )

    def set_chat_visible(self, visible: bool) -> None:
        self._toggle_chat_action.setText(
            _HIDE_CHAT_LABEL if visible else _SHOW_CHAT_LABEL,
        )
