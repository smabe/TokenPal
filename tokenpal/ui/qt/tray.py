"""System tray / menu-bar icon.

The tray menu is generated from a list of registered toggleable
windows handed in at construction (chat log, news, plus whatever
future window plugs in). Adding a new toggleable window means
appending one entry to the list — no new method on the tray.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtGui import QAction, QIcon, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from tokenpal.ui.palette import BUDDY_GREEN

_HIDE_BUDDY_LABEL = "Hide buddy"
_SHOW_BUDDY_LABEL = "Show buddy"


@dataclass(frozen=True)
class TrayWindow:
    """One toggleable log/info window the tray should expose."""

    name: str
    show_label: str
    hide_label: str
    on_toggle: Callable[[], None]


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
        windows: list[TrayWindow],
        on_options: Callable[[], None],
        on_quit: Callable[[], None],
        parent: QWidget | None = None,
        icon: QIcon | None = None,
    ) -> None:
        super().__init__(icon or _fallback_icon(), parent)
        self.setToolTip("TokenPal")

        menu = QMenu()

        self._toggle_buddy_action = QAction(_HIDE_BUDDY_LABEL, menu)
        self._toggle_buddy_action.triggered.connect(on_toggle_buddy)
        menu.addAction(self._toggle_buddy_action)

        self._window_actions: dict[str, QAction] = {}
        self._window_labels: dict[str, tuple[str, str]] = {}
        for spec in windows:
            action = QAction(spec.show_label, menu)
            action.triggered.connect(spec.on_toggle)
            menu.addAction(action)
            self._window_actions[spec.name] = action
            self._window_labels[spec.name] = (spec.show_label, spec.hide_label)

        menu.addSeparator()

        options_action = QAction("Options…", menu)
        options_action.triggered.connect(on_options)
        menu.addAction(options_action)

        menu.addSeparator()

        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(on_quit)
        menu.addAction(quit_action)

        self.setContextMenu(menu)
        # Retain a reference or Qt will garbage-collect the menu and the
        # tray click will pop up nothing on macOS.
        self._menu = menu

    def set_buddy_visible(self, visible: bool) -> None:
        self._toggle_buddy_action.setText(
            _HIDE_BUDDY_LABEL if visible else _SHOW_BUDDY_LABEL,
        )

    def set_window_visible(self, name: str, visible: bool) -> None:
        action = self._window_actions.get(name)
        if action is None:
            return
        show_label, hide_label = self._window_labels[name]
        action.setText(hide_label if visible else show_label)
