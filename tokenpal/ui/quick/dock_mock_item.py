"""Dock-mock as a QQuickItem child of the buddy pivot.

While the body is rotating, the real ``ChatDock`` (a ``QLineEdit``
host) cannot be rotated cleanly; the QWidget path snapshotted the
dock into a pixmap and painted the pixmap rotated inside a square
envelope. Under the Quick path, the parent pivot already supplies
the rotation, so we just present the snapshot as a textured quad
whose top-center sits at the foot anchor in pivot-local space.

Click-through: the item never accepts mouse events; whatever sits
under the dock area receives the cursor instead. Matches the
QWidget mock's ``WA_TransparentForMouseEvents`` semantics.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtQuick import QQuickItem, QSGSimpleTextureNode


class DockMockQuickItem(QQuickItem):
    def __init__(self) -> None:
        super().__init__()
        self.setFlag(QQuickItem.Flag.ItemHasContents, True)
        self.setVisible(False)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

        self._image: QImage | None = None
        self._texture = None
        self._tex_image_id: int | None = None
        self._anchor_parent = QPointF(0.0, 0.0)
        self._content_w = 0.0
        self._content_h = 0.0

    def set_source(self, pixmap: QPixmap) -> None:
        if pixmap is None or pixmap.isNull():
            self._image = None
            self.setVisible(False)
            self.update()
            return
        dpr = max(pixmap.devicePixelRatio(), 1.0)
        self._content_w = pixmap.width() / dpr
        self._content_h = pixmap.height() / dpr
        # Convert once on the GUI thread; updatePaintNode just uploads.
        # Fresh QImage gets a new id() so updatePaintNode's id-mismatch
        # branch will rebuild the texture.
        self._image = pixmap.toImage()
        self.setWidth(self._content_w)
        self.setHeight(self._content_h)
        self._reposition()
        self.update()

    def set_anchor_in_parent(self, x: float, y: float) -> None:
        """Park the top-center anchor at ``(x, y)`` in parent coords."""
        self._anchor_parent = QPointF(x, y)
        self._reposition()

    def set_visible(self, visible: bool) -> None:
        self.setVisible(visible)
        if visible:
            self.update()

    # Duck-type shims so QtOverlay can hold a Quick item in the same
    # ``self._dock_mock`` slot as the QWidget ``DockMock``.
    def show(self) -> None:
        self.set_visible(True)

    def hide(self) -> None:
        self.set_visible(False)

    def set_pose(self, _anchor_world, _angle_rad) -> None:
        return

    def close(self) -> None:
        self.set_visible(False)

    def _reposition(self) -> None:
        # Top-center sits at (width/2, 0) in item-local coords.
        self.setX(self._anchor_parent.x() - self._content_w / 2.0)
        self.setY(self._anchor_parent.y())

    def updatePaintNode(self, old_node, _data):
        if self._image is None:
            return None
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
