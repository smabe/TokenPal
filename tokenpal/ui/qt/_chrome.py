"""Shared frameless-window chrome: drag handle + zoom shortcuts.

Used by ``ChatHistoryWindow`` and ``NewsHistoryWindow`` — both are
frameless translucent windows that need a labeled grab strip and the
standard Cmd/Ctrl +/-/0 font-zoom keybinds.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QKeySequence, QMouseEvent, QShortcut
from PySide6.QtWidgets import QLabel, QWidget

from tokenpal.ui.qt._text_fx import apply_drop_shadow, glass_button_stylesheet

DRAG_HANDLE_HEIGHT = 22


class DragHandle(QLabel):
    """Thin labeled strip at the top of a frameless window that users
    grab to reposition. Walks up to the top-level window on press and
    moves it in the parent's coordinate space as the mouse moves.
    """

    def __init__(self, title: str, *, parent: QWidget) -> None:
        super().__init__(parent)
        self.setText(f"≡  {title}")
        self.setFixedHeight(DRAG_HANDLE_HEIGHT)
        self.setStyleSheet(glass_button_stylesheet(radius=8))
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )
        self._drag_offset: QPoint | None = None
        apply_drop_shadow(self, blur=6, offset=(0, 1))

    def set_title(self, title: str) -> None:
        self.setText(f"≡  {title}")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        win = self.window()
        if win is None:
            return
        self._drag_offset = (
            event.globalPosition().toPoint() - win.frameGeometry().topLeft()
        )
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is None:
            return
        win = self.window()
        if win is None:
            return
        new_pos = event.globalPosition().toPoint() - self._drag_offset
        win.move(new_pos)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._drag_offset = None
        event.accept()


def install_zoom_shortcuts(
    widget: QWidget, on_zoom: Callable[[int], None] | None,
) -> None:
    """Wire Cmd/Ctrl +/-/0 shortcuts that call ``on_zoom`` with +1, -1, 0.

    ``StandardKey.ZoomIn/ZoomOut`` maps to Cmd on macOS and Ctrl elsewhere.
    The reset binding uses ``Ctrl+0`` which Qt auto-remaps to Cmd+0 on macOS.
    No-op when ``on_zoom`` is None.
    """
    if on_zoom is None:
        return
    for key, delta in (
        (QKeySequence.StandardKey.ZoomIn, +1),
        (QKeySequence.StandardKey.ZoomOut, -1),
        (QKeySequence("Ctrl+0"), 0),
    ):
        QShortcut(key, widget).activated.connect(
            lambda d=delta: on_zoom(d),
        )
