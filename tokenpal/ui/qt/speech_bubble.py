"""Speech-bubble overlay widget for the Qt buddy.

Frameless + translucent + always-on-top. Positions itself above the
buddy window. Typing animation via QTimer so short bubbles feel snappy
and long ones don't dump a wall of text.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPaintEvent
from PySide6.QtWidgets import QWidget

_TYPING_INTERVAL_MS = 30
_BUBBLE_PADDING = 12
_BUBBLE_MAX_WIDTH = 360
# Slack for font-substitution mismatch: Qt's fontMetrics can under-report
# the advance for a substituted family, so wrap decisions fit a few
# pixels tighter than the visible bubble. Keeps the last word inside.
_BUBBLE_WRAP_SLACK = 28
_BUBBLE_RADIUS = 10

_BG = QColor(30, 30, 46, 232)
_FG = QColor("#ffffff")


class SpeechBubble(QWidget):
    """Floating bubble. Single-line to multi-line plain text, left-aligned."""

    def __init__(
        self,
        font_family: str = "Courier",
        font_size: int = 13,
    ) -> None:
        super().__init__()
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

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
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

    def _advance_typing(self) -> None:
        if len(self._visible_text) >= len(self._full_text):
            self._timer.stop()
            return
        self._visible_text = self._full_text[: len(self._visible_text) + 1]
        self._wrapped_cache_key = ("", 0)  # invalidate
        self.update()

    def _resize_for_text(self, text: str) -> None:
        fm = self.fontMetrics()
        lines = self._wrap_lines(text, fm)
        longest = max((fm.horizontalAdvance(line) for line in lines), default=0)
        line_h = fm.height()
        # Add wrap-slack to the widget width too: fontMetrics can
        # under-report the true pixel advance for a substituted family,
        # which would clip the last few pixels of the longest line at
        # the widget's right edge. Cap at MAX + slack so we don't grow
        # past the intended visual maximum.
        padded = min(longest + _BUBBLE_WRAP_SLACK, _BUBBLE_MAX_WIDTH)
        self.resize(
            padded + _BUBBLE_PADDING * 2,
            line_h * len(lines) + _BUBBLE_PADDING * 2,
        )

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

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(_BG)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(self.rect(), _BUBBLE_RADIUS, _BUBBLE_RADIUS)

        painter.setFont(self._font)
        painter.setPen(_FG)
        fm = self.fontMetrics()
        y = _BUBBLE_PADDING + fm.ascent()
        for line in self._wrap_lines(self._visible_text, fm):
            painter.drawText(_BUBBLE_PADDING, y, line)
            y += fm.height()
