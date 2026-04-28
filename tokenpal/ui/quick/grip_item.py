"""Resize-grip QQuickItem child of the buddy pivot.

Bottom-right corner of the hit rect sits at the item origin's
bottom-right (``(width, height)`` in item-local coords). The host
parks the item so that point coincides with the buddy's body-frame
bottom-right corner in pivot-local space; the pivot's rotation
swings the grip with the body.

The painted dots fill only a ``SIZE_GRIP_SIDE`` square in the
bottom-right of the hit rect. The remainder of the rect is rendered
as imperceptible-but-non-zero alpha so the click-through probe
treats the whole rotated rect as opaque (mirrors the QWidget path's
"alpha=1 fillRect" trick, just routed through our cursor-vs-alpha
sampling instead of layered-window per-pixel hit-test).
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtQuick import QQuickItem, QSGSimpleTextureNode

from tokenpal.ui.qt._chrome import (
    BUDDY_GRIP_HIT_SIDE,
    SIZE_GRIP_SIDE,
    _paint_diagonal_dots,
)


class GripQuickItem(QQuickItem):
    zoom_drag_delta = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.setFlag(QQuickItem.Flag.ItemHasContents, True)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)

        self._side = BUDDY_GRIP_HIT_SIDE
        self.setWidth(float(self._side))
        self.setHeight(float(self._side))

        self._anchor_parent = QPointF(0.0, 0.0)
        # Painted hit rect + dots are constant -- render once, reuse
        # the same QImage forever; updatePaintNode's id-equality check
        # then rebinds the existing QSGTexture each frame at zero cost.
        self._image: QImage = self._render_image()
        self._texture = None
        self._tex_image_id: int | None = None

        self._last_y: int | None = None

    def set_anchor_in_parent(self, x: float, y: float) -> None:
        """Park the bottom-right corner at ``(x, y)`` in parent coords."""
        self._anchor_parent = QPointF(x, y)
        self.setX(x - float(self._side))
        self.setY(y - float(self._side))

    def _render_image(self) -> QImage:
        side = self._side
        img = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        # Imperceptible alpha across the full hit rect so the cursor-
        # vs-alpha probe treats the whole rotated rect as clickable.
        p.fillRect(QRect(0, 0, side, side), QColor(0, 0, 0, 1))
        # Dots in the bottom-right corner.
        p.translate(side - SIZE_GRIP_SIDE, side - SIZE_GRIP_SIDE)
        _paint_diagonal_dots(p, SIZE_GRIP_SIDE)
        p.end()
        return img

    def updatePaintNode(self, old_node, _data):
        node = old_node
        if self._tex_image_id != id(self._image):
            self._texture = self.window().createTextureFromImage(self._image)
            self._tex_image_id = id(self._image)
            if node is None:
                node = QSGSimpleTextureNode()
            node.setTexture(self._texture)
            node.setOwnsTexture(True)
        elif node is None:
            node = QSGSimpleTextureNode()
            node.setTexture(self._texture)
            node.setOwnsTexture(False)
        node.setRect(QRectF(0.0, 0.0, self.width(), self.height()))
        return node

    def contains(self, point: QPointF) -> bool:
        return (
            0.0 <= point.x() <= float(self._side)
            and 0.0 <= point.y() <= float(self._side)
        )

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._last_y = int(event.globalPosition().toPoint().y())
        event.accept()

    def mouseMoveEvent(self, event):
        if self._last_y is None:
            return
        cur_y = int(event.globalPosition().toPoint().y())
        dy = cur_y - self._last_y
        if dy != 0:
            self.zoom_drag_delta.emit(dy)
            self._last_y = cur_y
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._last_y = None
        event.accept()

    # Duck-type shims so QtOverlay can hold a Quick item in the same
    # ``self._resize_grip`` slot as the QWidget ``BuddyResizeGrip``.
    def show(self) -> None:
        self.setVisible(True)
        self.update()

    def hide(self) -> None:
        self.setVisible(False)

    def set_pose(self, _anchor_world, _angle_rad) -> None:
        return

    def close(self) -> None:
        self.setVisible(False)
