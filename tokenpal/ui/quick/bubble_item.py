"""Speech bubble as a QQuickItem child of the buddy pivot.

The pivot already carries the lerped translation + body-frame
rotation; the bubble's tail just sits at a fixed pivot-local point
above the buddy's head and the rest of the body-aligned offset and
swing falls out of the parent transform. Compare with the QWidget
``SpeechBubble`` which had to ``move()`` + paint a rotated content
rect inside a square envelope every pump and clear the previous
frame manually -- the scene-graph composite handles all of that for
free here.

Rendering: the rounded-rect + text is drawn into a ``QImage`` cache
keyed on (visible_text, content size, font, colors). The cache turns
into a ``QSGTexture`` lazily on the render thread inside
``updatePaintNode``; per-frame paint is a textured-quad composite
with no QPainter on the hot path.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter
from PySide6.QtQuick import QQuickItem, QSGSimpleTextureNode

from tokenpal.config.chatlog_writer import (
    DEFAULT_BACKGROUND_COLOR,
    DEFAULT_FONT_COLOR,
    normalize_hex_color,
)
from tokenpal.config.schema import FontConfig
from tokenpal.ui.qt._text_fx import qt_font_from_config, scale_font
from tokenpal.ui.qt.speech_bubble import (
    _BUBBLE_BG_ALPHA,
    _BUBBLE_MAX_WIDTH,
    _BUBBLE_PADDING,
    _BUBBLE_RADIUS,
    _BUBBLE_WRAP_SLACK,
    _TYPING_INTERVAL_MS,
)


class BubbleQuickItem(QQuickItem):
    """Tail at item origin (top-left); content extends down/right.

    Item bounds are sized to the content rect, so Qt's mouse-event
    AABB pre-check matches what's painted. Hit testing uses the
    default ``contains`` (rect-inside) -- the rounded corners aren't
    worth the per-pixel alpha probe for a non-interactive surface.

    The parent (the buddy pivot in :class:`BuddyQuickWindow`) is
    positioned so that the bubble's tail anchor lands at the buddy's
    head plus the body-aligned hover offset. We size the item so that
    the tail anchor sits at ``(width/2, height)`` in item-local coords,
    and shift the item's position by ``(-content_w/2, -content_h)``
    relative to that anchor in parent coords. The host calls
    :meth:`set_anchor_in_parent` to re-park whenever the art changes.
    """

    def __init__(
        self, font_family: str = "Courier", font_size: int = 13,
    ) -> None:
        super().__init__()
        self.setFlag(QQuickItem.Flag.ItemHasContents, True)
        self.setVisible(False)

        self._bg_color = QColor(DEFAULT_BACKGROUND_COLOR)
        self._bg_color.setAlpha(_BUBBLE_BG_ALPHA)
        self._fg_color = QColor(DEFAULT_FONT_COLOR)
        self._base_font = QFont(font_family, font_size)
        self._base_font.setStyleHint(QFont.StyleHint.Monospace)
        self._zoom = 1.0
        self._font = QFont(self._base_font)

        self._full_text = ""
        self._visible_text = ""
        self._content_w = 0
        self._content_h = 0

        self._anchor_parent = QPointF(0.0, 0.0)

        self._image: QImage | None = None
        self._image_dirty = True
        self._texture = None
        self._tex_image_id: int | None = None

        self._timer = QTimer(self)
        self._timer.setInterval(_TYPING_INTERVAL_MS)
        self._timer.timeout.connect(self._advance_typing)

    def show_text(self, text: str, *, typing: bool = True) -> None:
        self._full_text = text
        if typing:
            self._visible_text = ""
            self._resize_for_text(text)
            self._timer.start()
        else:
            self._visible_text = text
            self._resize_for_text(text)
            self._timer.stop()
        self._image_dirty = True
        self.setVisible(True)
        self.update()

    def hide_bubble(self) -> None:
        self._timer.stop()
        self.setVisible(False)

    def set_background_color(self, hex_color: str) -> None:
        color = QColor(
            normalize_hex_color(hex_color, fallback=DEFAULT_BACKGROUND_COLOR),
        )
        color.setAlpha(_BUBBLE_BG_ALPHA)
        if color == self._bg_color:
            return
        self._bg_color = color
        self._image_dirty = True
        self.update()

    def set_font_color(self, hex_color: str) -> None:
        color = QColor(
            normalize_hex_color(hex_color, fallback=DEFAULT_FONT_COLOR),
        )
        if color == self._fg_color:
            return
        self._fg_color = color
        self._image_dirty = True
        self.update()

    def apply_font_config(
        self,
        cfg: FontConfig,
        *,
        fallback_family: str = "",
        fallback_size: int = 13,
    ) -> None:
        font = qt_font_from_config(
            cfg,
            fallback_family=fallback_family or self._base_font.family(),
            fallback_size=fallback_size,
        )
        font.setStyleHint(QFont.StyleHint.Monospace)
        self._base_font = font
        self._reapply_font()

    def set_zoom(self, factor: float) -> None:
        if factor <= 0.0 or factor == self._zoom:
            return
        self._zoom = factor
        self._reapply_font()

    def set_anchor_in_parent(self, x: float, y: float) -> None:
        """Park the tail at ``(x, y)`` in parent (pivot-local) coords."""
        self._anchor_parent = QPointF(x, y)
        self._reposition()

    def _reapply_font(self) -> None:
        self._font = scale_font(self._base_font, self._zoom)
        if self._full_text:
            self._resize_for_text(self._full_text)
        self._image_dirty = True
        self.update()

    def _advance_typing(self) -> None:
        if len(self._visible_text) >= len(self._full_text):
            self._timer.stop()
            return
        self._visible_text = self._full_text[: len(self._visible_text) + 1]
        self._image_dirty = True
        self.update()

    def _resize_for_text(self, text: str) -> None:
        fm = QFontMetrics(self._font)
        lines = self._wrap_lines(text, fm)
        longest = max((fm.horizontalAdvance(line) for line in lines), default=0)
        line_h = fm.height()
        padded = min(longest + _BUBBLE_WRAP_SLACK, _BUBBLE_MAX_WIDTH)
        self._content_w = padded + _BUBBLE_PADDING * 2
        self._content_h = line_h * len(lines) + _BUBBLE_PADDING * 2
        self.setWidth(float(self._content_w))
        self.setHeight(float(self._content_h))
        self._reposition()

    def _reposition(self) -> None:
        # Tail sits at (width/2, height) in item-local coords; place
        # the item so that point coincides with the parent-local anchor.
        self.setX(self._anchor_parent.x() - self._content_w / 2.0)
        self.setY(self._anchor_parent.y() - float(self._content_h))

    def _wrap_lines(self, text: str, fm: object) -> list[str]:
        # Visible text changes every typing tick, so a per-text cache
        # never hits during the typing animation. Just rewrap each call.
        lines: list[str] = []
        for paragraph in text.splitlines() or [""]:
            if not paragraph:
                lines.append("")
                continue
            current: list[str] = []
            width = 0
            for word in paragraph.split(" "):
                w = fm.horizontalAdvance(word + " ")  # type: ignore[attr-defined]
                if width + w > _BUBBLE_MAX_WIDTH - _BUBBLE_WRAP_SLACK and current:
                    lines.append(" ".join(current))
                    current = [word]
                    width = w
                else:
                    current.append(word)
                    width += w
            if current:
                lines.append(" ".join(current))
        return lines

    def _render_image(self) -> QImage | None:
        if self._content_w <= 0 or self._content_h <= 0:
            return None
        img = QImage(
            self._content_w, self._content_h, QImage.Format.Format_ARGB32_Premultiplied,
        )
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setBrush(self._bg_color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(
            QRectF(0.0, 0.0, float(self._content_w), float(self._content_h)),
            _BUBBLE_RADIUS, _BUBBLE_RADIUS,
        )
        p.setFont(self._font)
        p.setPen(self._fg_color)
        fm = QFontMetrics(self._font)
        y = _BUBBLE_PADDING + fm.ascent()
        for line in self._wrap_lines(self._visible_text, fm):
            p.drawText(_BUBBLE_PADDING, y, line)
            y += fm.height()
        p.end()
        return img

    def updatePaintNode(self, old_node, _data):
        if self._image_dirty or self._image is None:
            self._image = self._render_image()
            self._image_dirty = False
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

    def contains(self, point: QPointF) -> bool:
        if self._content_w <= 0 or self._content_h <= 0:
            return False
        return (
            0.0 <= point.x() <= self._content_w
            and 0.0 <= point.y() <= self._content_h
        )

    # Duck-type shims so QtOverlay can hold a Quick item in the same
    # ``self._bubble`` slot as the QWidget ``SpeechBubble``. The pivot
    # parent already supplies position + rotation, so ``set_pose`` is
    # a no-op; show/hide map to scene-graph visibility.
    def show(self) -> None:
        self.setVisible(True)
        self.update()

    def hide(self) -> None:
        self.setVisible(False)

    def set_pose(self, _tail_world, _angle_rad) -> None:
        return

    def close(self) -> None:
        self.setVisible(False)
