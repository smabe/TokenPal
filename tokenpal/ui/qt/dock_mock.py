"""Painted stand-in for :class:`ChatDock` used while the buddy is
rotating.

Native text widgets (``QLineEdit``) can't rotate with a parent's world
transform — caret position, focus ring, and IME break. So while the
body is swinging, we hide the real dock and show this mock instead:
a frameless transparent top-level window that holds a ``QPixmap``
snapshot of the real dock and paints it rotated around the foot
anchor. The overlay swaps back to the real dock as soon as the body
settles.

The mock is purely visual. ``WA_TransparentForMouseEvents`` passes
clicks through to whatever's underneath — there's nothing to interact
with anyway mid-swing.
"""

from __future__ import annotations

import math
import sys

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QPainter, QPaintEvent, QPixmap, QTransform
from PySide6.QtWidgets import QWidget

from tokenpal.ui.qt import _paint_trace

_ROTATION_MARGIN = 4


class DockMock(QWidget):
    """Paints a rotated snapshot of the real dock around a top-center
    anchor (the spot that normally sits just below the buddy's feet)."""

    def __init__(self) -> None:
        super().__init__()
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        # Mirror buddy_window: Qt.Tool on macOS auto-hides on app
        # deactivate. Off-darwin it's the right "no taskbar" hint.
        if sys.platform != "darwin":
            flags |= Qt.WindowType.Tool
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._pixmap: QPixmap | None = None
        self._anchor_widget: tuple[int, int] = (0, 0)
        self._angle_rad = 0.0

    def set_source(self, pixmap: QPixmap) -> None:
        """Install a fresh snapshot of the real dock. The top-center of
        ``pixmap`` becomes the anchor that tracks the foot position."""
        self._pixmap = pixmap
        dpr = max(pixmap.devicePixelRatio(), 1.0)
        pw = pixmap.width() / dpr
        ph = pixmap.height() / dpr
        radius = math.hypot(pw / 2.0, ph)
        size = int(math.ceil(2 * radius)) + _ROTATION_MARGIN * 2
        self._anchor_widget = (size // 2, size // 2)
        self.resize(size, size)
        self.update()

    def set_pose(self, anchor_world: QPointF, angle_rad: float) -> None:
        """Move the window so the pixmap's top-center lands at
        ``anchor_world`` and paint the snapshot rotated by ``angle_rad``."""
        self._angle_rad = angle_rad
        ax, ay = self._anchor_widget
        new_x = int(anchor_world.x()) - ax
        new_y = int(anchor_world.y()) - ay
        pos = self.pos()
        if pos.x() != new_x or pos.y() != new_y:
            self.move(new_x, new_y)
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        if self._pixmap is None or self._pixmap.isNull():
            return
        if _paint_trace.enabled():
            ax, ay = self._anchor_widget
            pos = self.pos()
            _paint_trace.log_paint(
                "dock_mock",
                theta=self._angle_rad,
                pos=(float(pos.x() + ax), float(pos.y() + ay)),
            )
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        ax, ay = self._anchor_widget
        dpr = max(self._pixmap.devicePixelRatio(), 1.0)
        pw = self._pixmap.width() / dpr
        t = QTransform()
        t.translate(ax, ay)
        t.rotate(math.degrees(self._angle_rad))
        t.translate(-pw / 2.0, 0.0)
        painter.setWorldTransform(t)
        painter.drawPixmap(0, 0, self._pixmap)
