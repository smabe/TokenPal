"""Shared frameless-window chrome: drag handle + zoom shortcuts.

Used by ``ChatHistoryWindow`` and ``NewsHistoryWindow`` — both are
frameless translucent windows that need a labeled grab strip and the
standard Cmd/Ctrl +/-/0 font-zoom keybinds.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtCore import QPoint, QPointF, QRect, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QRegion,
    QShortcut,
    QShowEvent,
    QTransform,
)
from PySide6.QtWidgets import QLabel, QSizeGrip, QWidget

from tokenpal.ui.qt import _paint_trace
from tokenpal.ui.qt._text_fx import apply_drop_shadow, glass_button_stylesheet
from tokenpal.ui.qt.platform import buddy_overlay_flags

DRAG_HANDLE_HEIGHT = 22
SIZE_GRIP_SIDE = 16
# BuddyResizeGrip widget is bigger than its painted dots so the hit
# area extends inward — the 16-px dot pattern alone is too small to
# grab reliably without flush-edge precision.
BUDDY_GRIP_HIT_SIDE = 48
GRIP_DOT_ROWS = 3
GRIP_DOT_SPACING = 5
GRIP_DOT_INSET = 3
# Slack around the rotation envelope so antialiased dot edges don't
# clip when the grip is tilted. Same convention as SpeechBubble.
_GRIP_ROTATION_MARGIN = 4


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

    Top-level frameless translucent widget — same shape as
    ``SpeechBubble``. The widget is a square big enough to contain the
    BUDDY_GRIP_HIT_SIDE hit-rect rotated to any angle around its
    bottom-right corner; ``set_pose(anchor_world, angle_rad)`` parks
    that corner on the buddy's body-frame bottom-right and rotates the
    painted dots to match. Pure paint (no native children) means
    ``painter.setWorldTransform`` rotates everything cleanly without
    the snapshot-and-park dance ChatDock needs.
    """

    zoom_drag_delta = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(buddy_overlay_flags())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)

        # Widget is a square big enough to contain a BUDDY_GRIP_HIT_SIDE
        # square rotated to any angle around its bottom-right corner.
        # Worst-case: opposite corner (top-left) at distance hypot(s, s).
        radius = math.hypot(BUDDY_GRIP_HIT_SIDE, BUDDY_GRIP_HIT_SIDE)
        size = int(math.ceil(2 * radius)) + _GRIP_ROTATION_MARGIN * 2
        self.setFixedSize(size, size)
        self._anchor_widget: tuple[int, int] = (size // 2, size // 2)
        self._angle_rad = 0.0
        self._last_y: int | None = None

    def set_pose(self, anchor_world: QPointF, angle_rad: float) -> None:
        """Move so the dot pattern's bottom-right corner lands at
        ``anchor_world`` and paint the dots rotated by ``angle_rad``.
        Called by the overlay on every ``position_changed`` so the
        grip stays glued to the buddy's body-frame bottom-right
        regardless of swing.

        Synchronous ``repaint()`` so the grip paint lands in the same
        DWM composite frame as the buddy paint — see SpeechBubble.set_pose
        for the full reasoning."""
        prev_angle = self._angle_rad
        self._angle_rad = angle_rad
        ax, ay = self._anchor_widget
        new_x = int(anchor_world.x()) - ax
        new_y = int(anchor_world.y()) - ay
        pos = self.pos()
        moved = pos.x() != new_x or pos.y() != new_y
        if moved:
            self.move(new_x, new_y)
        if prev_angle != angle_rad:
            self._update_click_mask()
            self.repaint()
        elif moved:
            self.repaint()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._update_click_mask()

    def _update_click_mask(self) -> None:
        # Mask follows the rotated hit-rect so the cursor only flips
        # to SizeFDiagCursor over the actual rotated dot square,
        # never over the surrounding rotation-envelope padding.
        ax, ay = self._anchor_widget
        side = float(BUDDY_GRIP_HIT_SIDE)
        t = QTransform()
        t.translate(ax, ay)
        t.rotate(math.degrees(self._angle_rad))
        t.translate(-side, -side)
        corners = (
            t.map(QPointF(0.0, 0.0)),
            t.map(QPointF(side, 0.0)),
            t.map(QPointF(0.0, side)),
            t.map(QPointF(side, side)),
        )
        xs = [p.x() for p in corners]
        ys = [p.y() for p in corners]
        x = int(math.floor(min(xs))) - 1
        y = int(math.floor(min(ys))) - 1
        x2 = int(math.ceil(max(xs))) + 1
        y2 = int(math.ceil(max(ys))) + 1
        self.setMask(QRegion(QRect(x, y, max(x2 - x, 1), max(y2 - y, 1))))

    def paintEvent(self, _event: QPaintEvent) -> None:
        if _paint_trace.enabled():
            ax_t, ay_t = self._anchor_widget
            pos_t = self.pos()
            _paint_trace.log_paint(
                "grip",
                theta=self._angle_rad,
                pos=(float(pos_t.x() + ax_t), float(pos_t.y() + ay_t)),
            )
        painter = QPainter(self)
        # Fixed-size square; rotated dots fill only a sub-rect so
        # previous-frame pixels persist without an explicit clear.
        # See SpeechBubble.paintEvent for the full reasoning.
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_Clear,
        )
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_SourceOver,
        )
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        ax, ay = self._anchor_widget
        side = BUDDY_GRIP_HIT_SIDE
        t = QTransform()
        t.translate(ax, ay)
        t.rotate(math.degrees(self._angle_rad))
        t.translate(-float(side), -float(side))
        painter.setWorldTransform(t)
        # Imperceptible-but-non-zero alpha across the full hit rect so
        # the OS layered-window hit test (which on Windows routes
        # clicks by per-pixel alpha, not widget bounds) treats the
        # entire rotated rect as clickable. Without this, only the
        # painted dots register.
        painter.fillRect(QRect(0, 0, side, side), QColor(0, 0, 0, 1))
        # Dots in the bottom-right corner of the hit rect.
        painter.translate(side - SIZE_GRIP_SIDE, side - SIZE_GRIP_SIDE)
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
