"""Shared frameless-window chrome: drag handle + zoom shortcuts.

Used by ``ChatHistoryWindow`` and ``NewsHistoryWindow`` — both are
frameless translucent windows that need a labeled grab strip and the
standard Cmd/Ctrl +/-/0 font-zoom keybinds.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QMouseEvent, QPainter, QPaintEvent, QShortcut
from PySide6.QtWidgets import QLabel, QSizeGrip, QWidget

from tokenpal.ui.qt._text_fx import apply_drop_shadow, glass_button_stylesheet

DRAG_HANDLE_HEIGHT = 22
SIZE_GRIP_SIDE = 16
# BuddyResizeGrip widget is bigger than its painted dots so the hit
# area extends inward — the 16-px dot pattern alone is too small to
# grab reliably without flush-edge precision.
BUDDY_GRIP_HIT_SIDE = 48
GRIP_DOT_ROWS = 3
GRIP_DOT_SPACING = 5
GRIP_DOT_INSET = 3


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


def _paint_diagonal_dots(painter: QPainter, side: int) -> None:
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor.fromRgbF(1.0, 1.0, 1.0, 0.4))
    for row in range(GRIP_DOT_ROWS):
        for col in range(GRIP_DOT_ROWS - row):
            cx = side - GRIP_DOT_INSET - col * GRIP_DOT_SPACING
            cy = side - GRIP_DOT_INSET - row * GRIP_DOT_SPACING
            painter.drawEllipse(QPoint(cx, cy), 1, 1)


class BuddyResizeGrip(QWidget):
    """Bottom-right corner grip on the buddy. Drag y emits per-pixel
    deltas via ``zoom_drag_delta``; the overlay integrates them into a
    clamped zoom factor.

    The widget is larger than the painted dots so the hit area extends
    inward — flush-edge 16-px dots alone are too fiddly to grab."""

    zoom_drag_delta = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(BUDDY_GRIP_HIT_SIDE, BUDDY_GRIP_HIT_SIDE)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self._last_y: int | None = None

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        # Imperceptible-but-non-zero alpha across the full widget so the
        # OS layered-window hit test (which on Windows routes clicks by
        # per-pixel alpha, not widget bounds) treats the entire rect as
        # clickable. Without this, only the painted dots register and
        # the bigger widget size buys nothing.
        painter.fillRect(self.rect(), QColor(0, 0, 0, 1))
        offset = BUDDY_GRIP_HIT_SIDE - SIZE_GRIP_SIDE
        painter.translate(offset, offset)
        _paint_diagonal_dots(painter, SIZE_GRIP_SIDE)
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._last_y = event.globalPosition().toPoint().y()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._last_y is None:
            return
        cur_y = event.globalPosition().toPoint().y()
        dy = cur_y - self._last_y
        if dy != 0:
            self.zoom_drag_delta.emit(dy)
            self._last_y = cur_y
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._last_y = None
        event.accept()


class GlassSizeGrip(QSizeGrip):
    """``QSizeGrip`` with a soft-white dotted paint that fits the glass aesthetic."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFixedSize(SIZE_GRIP_SIDE, SIZE_GRIP_SIDE)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        _paint_diagonal_dots(painter, SIZE_GRIP_SIDE)
        painter.end()


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
