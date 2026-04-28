"""QQuickItem rendering the buddy's master pixmap as a textured quad.

Reads art geometry, master pixmap, and lerped state from a
``BuddyCore``. Rotation around the head-heavy COM is handled by the
parent pivot item in ``BuddyQuickWindow`` (QQuickItem's
TransformOrigin enum has only nine discrete points; for an arbitrary
COM we use a parent-item pivot positioned at COM in window coords,
with the buddy item offset by ``-com_art`` from the pivot's origin).

``updatePaintNode`` runs on the render thread. The master pixmap is
invariant per (frame_lines, font, zoom), so reading it from the render
thread is safe.
"""
from __future__ import annotations

import time
from collections import deque

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtQuick import QQuickItem, QSGSimpleTextureNode

from tokenpal.ui.buddy_core import BuddyCore


class BuddyQuickItem(QQuickItem):
    def __init__(self, core: BuddyCore):
        super().__init__()
        self._core = core
        self._cached_pixmap_id: int | None = None
        self._texture = None
        self.setFlag(QQuickItem.Flag.ItemHasContents, True)
        self.setAcceptedMouseButtons(
            Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton
        )
        self.paint_samples_ms: deque[float] = deque(maxlen=600)

    def updatePaintNode(self, old_node, _data):
        t0 = time.perf_counter()
        node = old_node
        pm = self._core.render_art_pixmap()
        if self._cached_pixmap_id != id(pm) or self._texture is None:
            img = pm.toImage()
            self._texture = self.window().createTextureFromImage(img)
            self._cached_pixmap_id = id(pm)
            if node is None:
                node = QSGSimpleTextureNode()
            node.setTexture(self._texture)
            node.setOwnsTexture(True)
        elif node is None:
            node = QSGSimpleTextureNode()
            node.setTexture(self._texture)
            node.setOwnsTexture(False)
        node.setRect(QRectF(0.0, 0.0, self.width(), self.height()))
        self.paint_samples_ms.append((time.perf_counter() - t0) * 1000.0)
        return node

    def contains(self, point: QPointF) -> bool:
        # event positions are passed in item-local coords AFTER Qt has
        # inverted the parent pivot's rotation, so the local point is
        # equivalent to an art-frame coord (with the item sized to
        # art_w x art_h).
        if 0.0 <= point.x() <= self.width() and 0.0 <= point.y() <= self.height():
            return self._core.is_painted_cell_at(point.x(), point.y())
        return False

    def mousePressEvent(self, event):
        c = self._core
        if event.button() == Qt.MouseButton.RightButton:
            handler = c.right_click_handler
            if handler is not None:
                handler(event.globalPosition().toPoint())
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        art = event.position()
        if not c.is_painted_cell_at(art.x(), art.y()):
            event.ignore()
            return
        c.begin_drag(QPointF(art.x(), art.y()), event.globalPosition())
        event.accept()

    def mouseMoveEvent(self, event):
        c = self._core
        if not c.is_dragging():
            return
        cursor = event.globalPosition()
        c.set_grab_target(cursor.x(), cursor.y())

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._core.end_drag()
