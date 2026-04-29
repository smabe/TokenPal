"""Shared base for frameless translucent log windows.

``ChatHistoryWindow`` and ``NewsHistoryWindow`` both want the same
chrome (drag handle · scrollable log · hide button · rounded
translucent background · live opacity / bg / fg / font setters).
This base owns that surface; subclasses add their own append APIs
plus any extra layout slots they need.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from tokenpal.config.chatlog_writer import (
    DEFAULT_BACKGROUND_COLOR,
    DEFAULT_FONT_COLOR,
    clamp_background_opacity,
    normalize_hex_color,
)
from tokenpal.ui.qt._chrome import DragHandle, GlassSizeGrip, install_zoom_shortcuts
from tokenpal.ui.qt._text_fx import (
    apply_drop_shadow,
    glass_button_stylesheet,
    glass_scrollbar_stylesheet,
)
from tokenpal.ui.qt.platform import buddy_overlay_flags


class TranslucentLogWindow(QWidget):
    """Frameless translucent scrollable log with shared chat-style chrome."""

    LOG_MAX_LINES: int = 500

    def __init__(
        self,
        *,
        title: str,
        default_size: tuple[int, int] = (520, 380),
        on_hide: Callable[[], None] | None = None,
        on_zoom: Callable[[int], None] | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle(title)
        self.resize(*default_size)
        self._on_hide = on_hide
        install_zoom_shortcuts(self, on_zoom)

        self.setWindowFlags(buddy_overlay_flags(focusable=True))
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        self._drag_handle = DragHandle(title, parent=self)
        layout.addWidget(self._drag_handle, 0)

        self._log = QTextBrowser(self)
        self._log.setOpenExternalLinks(True)
        self._log.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._log.viewport().setAutoFillBackground(False)
        self._log.setFrameShape(QTextBrowser.Shape.NoFrame)
        self._font_color: str = DEFAULT_FONT_COLOR
        self._apply_log_stylesheet()
        self._background_opacity: float = 0.0
        self._background_color: QColor = QColor(DEFAULT_BACKGROUND_COLOR)
        self._background_brush = QBrush(QColor(0, 0, 0, 0))
        # Symmetric glow rather than a directional drop shadow: offset
        # (0, 0) with a short blur radius gives a dense halo wrapping
        # every glyph on all sides.
        apply_drop_shadow(
            self._log.viewport(),
            blur=4,
            offset=(0, 0),
            color=QColor(0, 0, 0, 255),
        )
        layout.addWidget(self._log, 1)

        # Subclasses can mount extra widgets between the log and the
        # hide button (e.g. ChatHistoryWindow parks its embedded dock
        # in this slot when the buddy is hidden).
        self._extras_layout = QVBoxLayout()
        self._extras_layout.setContentsMargins(0, 0, 0, 0)
        self._extras_layout.setSpacing(0)
        layout.addLayout(self._extras_layout)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self._hide_button = QPushButton("Hide", self)
        self._hide_button.setFixedHeight(26)
        self._hide_button.setStyleSheet(glass_button_stylesheet())
        apply_drop_shadow(self._hide_button, blur=8, offset=(0, 1))
        self._hide_button.clicked.connect(self._handle_hide_clicked)
        row.addWidget(self._hide_button, 0, Qt.AlignmentFlag.AlignLeft)
        row.addStretch(1)
        self._size_grip = GlassSizeGrip(self)
        row.addWidget(
            self._size_grip, 0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
        )
        layout.addLayout(row)

    def _handle_hide_clicked(self) -> None:
        if self._on_hide is not None:
            self._on_hide()
        else:
            self.hide()

    def set_background_opacity(self, opacity: float) -> None:
        self._background_opacity = clamp_background_opacity(opacity)
        self._rebuild_background_brush()

    def set_background_color(self, hex_color: str) -> None:
        normalized = normalize_hex_color(
            hex_color, fallback=DEFAULT_BACKGROUND_COLOR,
        )
        if QColor(normalized) == self._background_color:
            return
        self._background_color = QColor(normalized)
        self._rebuild_background_brush()

    def set_font_color(self, hex_color: str) -> None:
        normalized = normalize_hex_color(
            hex_color, fallback=DEFAULT_FONT_COLOR,
        )
        if normalized == self._font_color:
            return
        self._font_color = normalized
        self._apply_log_stylesheet()

    def apply_font(self, font: QFont) -> None:
        self._log.setFont(font)

    def _rebuild_background_brush(self) -> None:
        color = QColor(self._background_color)
        color.setAlpha(int(round(self._background_opacity * 255)))
        self._background_brush = QBrush(color)
        self.update()

    def _apply_log_stylesheet(self) -> None:
        self._log.setStyleSheet(
            f"QTextBrowser {{ background: transparent; "
            f"color: {self._font_color}; padding: 8px; }}"
            + glass_scrollbar_stylesheet()
        )

    def paintEvent(self, event: QPaintEvent) -> None:
        if self._background_opacity > 0.0:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._background_brush)
            painter.drawRoundedRect(QRectF(self.rect()), 10.0, 10.0)
            painter.end()
        super().paintEvent(event)

    def _trim_to_cap(self) -> None:
        doc = self._log.document()
        while doc.blockCount() > self.LOG_MAX_LINES:
            cursor = self._log.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            cursor.select(cursor.SelectionType.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()
