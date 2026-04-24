"""Speech-bubble overlay widget for the Qt buddy.

Frameless + translucent + always-on-top. Positions itself above the
buddy window. Typing animation via QTimer so short bubbles feel snappy
and long ones don't dump a wall of text.

The bubble rotates with the buddy when he swings: the *content* rect
(rounded rect + text) stays the size the text needs, but the window
rect is expanded to a square large enough to contain that content
rotated any amount around the bubble's tail anchor (bottom-center).
``set_pose`` positions the window so the tail lands on the buddy's
head in world coords and paints everything rotated by ``body_angle``.
"""

from __future__ import annotations

import math
import sys

from PySide6.QtCore import QPointF, QRect, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPaintEvent,
    QRegion,
    QShowEvent,
    QTransform,
)
from PySide6.QtWidgets import QWidget

from tokenpal.config.chatlog_writer import (
    DEFAULT_BACKGROUND_COLOR,
    DEFAULT_FONT_COLOR,
    normalize_hex_color,
)
from tokenpal.config.schema import FontConfig
from tokenpal.ui.qt._text_fx import qt_font_from_config

_TYPING_INTERVAL_MS = 30
_BUBBLE_PADDING = 12
_BUBBLE_MAX_WIDTH = 360
# Slack for font-substitution mismatch: Qt's fontMetrics can under-report
# the advance for a substituted family, so wrap decisions fit a few
# pixels tighter than the visible bubble. Keeps the last word inside.
_BUBBLE_WRAP_SLACK = 28
_BUBBLE_RADIUS = 10
# Extra pixels around the rotation bounding circle so antialiasing at
# the edges doesn't clip when the bubble is tilted.
_ROTATION_MARGIN = 4

# Bubble bg keeps a fixed alpha so it stays readable even when the chat
# history panel is set to fully transparent. Only the RGB channels track
# the user's chat_log background_color pick.
_BUBBLE_BG_ALPHA = 232


class SpeechBubble(QWidget):
    """Floating bubble. Single-line to multi-line plain text, left-aligned."""

    def __init__(
        self,
        font_family: str = "Courier",
        font_size: int = 13,
    ) -> None:
        super().__init__()
        self._bg_color = QColor(DEFAULT_BACKGROUND_COLOR)
        self._bg_color.setAlpha(_BUBBLE_BG_ALPHA)
        self._fg_color = QColor(DEFAULT_FONT_COLOR)
        self._font = QFont(font_family, font_size)
        self._font.setStyleHint(QFont.StyleHint.Monospace)
        # Must apply to the widget itself, not just the painter, so
        # self.fontMetrics() measures the same font we later paint with.
        # Otherwise wrap uses proportional-font advances while paint
        # renders with monospace, and the longest line overflows.
        self.setFont(self._font)
        self._full_text = ""
        self._visible_text = ""
        self._wrapped_cache: list[str] = []
        self._wrapped_cache_key: tuple[str, int] = ("", 0)
        # Content rect (the rounded rect + text) is sized to fit the
        # text. The widget itself is a square big enough to contain the
        # content rotated any amount around its tail anchor — see
        # ``_resize_for_text`` for the geometry.
        self._content_w = 0
        self._content_h = 0
        self._tail_widget: tuple[int, int] = (0, 0)
        self._body_angle_rad = 0.0

        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        # Mirror buddy_window: Qt.Tool on macOS maps to an NSWindow
        # utility-panel that auto-hides on app deactivate, so the bubble
        # would vanish whenever the user clicked away. Off-darwin Tool is
        # the right "no taskbar entry" hint.
        if sys.platform != "darwin":
            flags |= Qt.WindowType.Tool
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._timer = QTimer(self)
        self._timer.setInterval(_TYPING_INTERVAL_MS)
        self._timer.timeout.connect(self._advance_typing)

    def show_text(self, text: str, *, typing: bool = True) -> None:
        """Reveal ``text`` in the bubble. If ``typing``, animate
        character-by-character; otherwise render it instantly."""
        self._full_text = text
        if typing:
            self._visible_text = ""
            self._resize_for_text(text)
            self._timer.start()
        else:
            self._visible_text = text
            self._resize_for_text(text)
            self._timer.stop()
        self.show()
        self.update()

    def hide_bubble(self) -> None:
        self._timer.stop()
        self.hide()

    def set_background_color(self, hex_color: str) -> None:
        """Update the bubble fill RGB. Alpha stays at ``_BUBBLE_BG_ALPHA``
        so a fully-transparent chat history doesn't erase the bubble."""
        color = QColor(
            normalize_hex_color(hex_color, fallback=DEFAULT_BACKGROUND_COLOR),
        )
        color.setAlpha(_BUBBLE_BG_ALPHA)
        if color == self._bg_color:
            return
        self._bg_color = color
        self.update()

    def set_font_color(self, hex_color: str) -> None:
        color = QColor(
            normalize_hex_color(hex_color, fallback=DEFAULT_FONT_COLOR),
        )
        if color == self._fg_color:
            return
        self._fg_color = color
        self.update()

    def apply_font_config(
        self,
        cfg: FontConfig,
        *,
        fallback_family: str = "",
        fallback_size: int = 13,
    ) -> None:
        """Replace the bubble font and re-layout. Keeps the monospace
        style hint so the wrap math keeps using a fixed-advance metric
        when ``cfg.family`` is empty."""
        font = qt_font_from_config(
            cfg,
            fallback_family=fallback_family or self._font.family(),
            fallback_size=fallback_size,
        )
        font.setStyleHint(QFont.StyleHint.Monospace)
        self._font = font
        self.setFont(font)
        # fontMetrics changed — wrap cache and widget size are stale.
        self._wrapped_cache_key = ("", 0)
        if self._full_text:
            self._resize_for_text(self._full_text)
        self.update()

    def _advance_typing(self) -> None:
        if len(self._visible_text) >= len(self._full_text):
            self._timer.stop()
            return
        self._visible_text = self._full_text[: len(self._visible_text) + 1]
        self._wrapped_cache_key = ("", 0)  # invalidate
        self.update()

    def _resize_for_text(self, text: str) -> None:
        """Size the content rect to fit the text, then pick a window
        rect square that's large enough to hold the content rotated any
        amount around its tail anchor (bottom-center of content).

        The tail lands at ``(radius, radius)`` in widget coords; the
        content's top-left sits at ``(radius - content_w/2, radius -
        content_h)`` at angle 0, and stays within the window rect at
        any rotation because ``radius`` is the max distance from the
        tail to any content corner.
        """
        fm = self.fontMetrics()
        lines = self._wrap_lines(text, fm)
        longest = max((fm.horizontalAdvance(line) for line in lines), default=0)
        line_h = fm.height()
        # Add wrap-slack to the content width too: fontMetrics can
        # under-report the true pixel advance for a substituted family,
        # which would clip the last few pixels of the longest line at
        # the content's right edge. Cap at MAX + slack so we don't grow
        # past the intended visual maximum.
        padded = min(longest + _BUBBLE_WRAP_SLACK, _BUBBLE_MAX_WIDTH)
        self._content_w = padded + _BUBBLE_PADDING * 2
        self._content_h = line_h * len(lines) + _BUBBLE_PADDING * 2

        radius = math.hypot(self._content_w / 2.0, float(self._content_h))
        size = int(math.ceil(2 * radius)) + _ROTATION_MARGIN * 2
        self._tail_widget = (size // 2, size // 2)
        self.resize(size, size)
        self._update_click_mask()

    def _wrap_lines(self, text: str, fm: object) -> list[str]:
        key = (text, self.width())
        if key == self._wrapped_cache_key:
            return self._wrapped_cache
        # Simple whitespace wrap. Preserve paragraph breaks in the input.
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
        self._wrapped_cache = lines
        self._wrapped_cache_key = key
        return lines

    def set_pose(self, tail_world: QPointF, angle_rad: float) -> None:
        """Position the window so the bubble's tail lands at
        ``tail_world`` and paint the content rotated by ``angle_rad``.

        Called by the overlay whenever the buddy's head moves or
        rotates. Rotation is around the tail — the bubble swings like
        it's on a string attached to the buddy's head.
        """
        prev_angle = self._body_angle_rad
        self._body_angle_rad = angle_rad
        tx, ty = self._tail_widget
        new_x = int(tail_world.x()) - tx
        new_y = int(tail_world.y()) - ty
        pos = self.pos()
        if pos.x() != new_x or pos.y() != new_y:
            self.move(new_x, new_y)
        if prev_angle != angle_rad:
            self._update_click_mask()
        self.update()

    def showEvent(self, event: QShowEvent) -> None:
        """Apply the click-through mask once the native window is
        mapped. The widget is a rotation-padded square — without a
        mask, its transparent padding extends over the buddy's body
        and intercepts clicks that should reach the buddy."""
        super().showEvent(event)
        self._update_click_mask()

    def _update_click_mask(self) -> None:
        """Restrict clickable area to the rotated content rect's AABB.

        The rounded-rect bubble lives in a widget square sized for
        arbitrary rotation around the tail anchor. Everything outside
        the content rect is transparent padding; masking it away lets
        clicks fall through to the buddy underneath.
        """
        if self._content_w <= 0 or self._content_h <= 0:
            return
        tx, ty = self._tail_widget
        t = QTransform()
        t.translate(tx, ty)
        t.rotate(math.degrees(self._body_angle_rad))
        t.translate(-self._content_w / 2.0, -float(self._content_h))
        corners = (
            t.map(QPointF(0.0, 0.0)),
            t.map(QPointF(float(self._content_w), 0.0)),
            t.map(QPointF(0.0, float(self._content_h))),
            t.map(QPointF(float(self._content_w), float(self._content_h))),
        )
        xs = [p.x() for p in corners]
        ys = [p.y() for p in corners]
        x = int(math.floor(min(xs))) - 1
        y = int(math.floor(min(ys))) - 1
        x2 = int(math.ceil(max(xs))) + 1
        y2 = int(math.ceil(max(ys))) + 1
        self.setMask(QRegion(QRect(x, y, max(x2 - x, 1), max(y2 - y, 1))))

    def paintEvent(self, _event: QPaintEvent) -> None:
        if self._content_w <= 0 or self._content_h <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        # Transform: widget origin → tail point → rotate → content's top-
        # left. After this, painting at (0, 0) to (content_w, content_h)
        # draws the bubble with its tail at the widget's center, rotated
        # around that tail.
        tx, ty = self._tail_widget
        t = QTransform()
        t.translate(tx, ty)
        t.rotate(math.degrees(self._body_angle_rad))
        t.translate(-self._content_w / 2.0, -float(self._content_h))
        painter.setWorldTransform(t)

        content_rect = self.rect().adjusted(0, 0, 0, 0)
        content_rect.setWidth(self._content_w)
        content_rect.setHeight(self._content_h)

        painter.setBrush(self._bg_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(content_rect, _BUBBLE_RADIUS, _BUBBLE_RADIUS)

        painter.setFont(self._font)
        painter.setPen(self._fg_color)
        fm = self.fontMetrics()
        y = _BUBBLE_PADDING + fm.ascent()
        for line in self._wrap_lines(self._visible_text, fm):
            painter.drawText(_BUBBLE_PADDING, y, line)
            y += fm.height()
