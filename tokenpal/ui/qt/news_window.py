"""News history window — frameless translucent log of every world-news
headline the buddy has picked up this session. Same chrome as
``ChatHistoryWindow``; one row per headline with source badge,
clickable link, and small meta line.
"""

from __future__ import annotations

from collections.abc import Callable
from html import escape

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPaintEvent,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from tokenpal.brain.news_buffer import NewsItem
from tokenpal.config.chatlog_writer import (
    DEFAULT_BACKGROUND_COLOR,
    DEFAULT_FONT_COLOR,
    clamp_background_opacity,
    normalize_hex_color,
)
from tokenpal.ui.qt._chrome import DragHandle, install_zoom_shortcuts
from tokenpal.ui.qt._text_fx import (
    apply_drop_shadow,
    glass_button_stylesheet,
    glass_scrollbar_stylesheet,
    transparent_window_flags,
)

_DEFAULT_SIZE = (520, 420)
_LOG_MAX_LINES = 600
_SOURCE_LABELS: dict[str, str] = {
    "world_awareness": "HN",
    "lobsters": "Lobsters",
    "github_trending": "GitHub",
}
_SOURCE_COLORS: dict[str, str] = {
    "world_awareness": "#ff8a4c",
    "lobsters": "#a06a4a",
    "github_trending": "#9aa6ff",
}


class NewsHistoryWindow(QWidget):
    """Frameless translucent scrollable list of news headlines.
    Starts hidden — the tray "Show news" toggle reveals it.
    """

    def __init__(
        self,
        *,
        title: str = "News",
        on_hide: Callable[[], None] | None = None,
        on_zoom: Callable[[int], None] | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle(title)
        self.resize(*_DEFAULT_SIZE)
        self._on_hide = on_hide
        install_zoom_shortcuts(self, on_zoom)

        self.setWindowFlags(transparent_window_flags())
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
        apply_drop_shadow(
            self._log.viewport(),
            blur=4,
            offset=(0, 0),
            color=QColor(0, 0, 0, 255),
        )
        layout.addWidget(self._log, 1)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self._hide_button = QPushButton("Hide", self)
        self._hide_button.setFixedHeight(26)
        self._hide_button.setStyleSheet(glass_button_stylesheet())
        apply_drop_shadow(self._hide_button, blur=8, offset=(0, 1))
        self._hide_button.clicked.connect(self._handle_hide_clicked)
        row.addWidget(self._hide_button, 0, Qt.AlignmentFlag.AlignLeft)
        row.addStretch(1)
        layout.addLayout(row)

        self._empty_state_shown = False
        self._show_empty_state()

    def _handle_hide_clicked(self) -> None:
        if self._on_hide is not None:
            self._on_hide()
        else:
            self.hide()

    def append_items(self, items: list[NewsItem]) -> None:
        if not items:
            return
        if self._empty_state_shown:
            self._log.clear()
            self._empty_state_shown = False
        for item in items:
            self._log.append(_format_row(item, self._font_color))
        self._trim_to_cap()

    def clear(self) -> None:
        self._log.clear()
        self._show_empty_state()

    def apply_font(self, font: QFont) -> None:
        self._log.setFont(font)

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
        while doc.blockCount() > _LOG_MAX_LINES:
            cursor = self._log.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            cursor.select(cursor.SelectionType.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()

    def _show_empty_state(self) -> None:
        self._log.append(
            '<div style="margin: 8px 0; color: #888888; font-style: italic">'
            "Headlines will land here as your buddy notices them."
            "</div>"
        )
        self._empty_state_shown = True


def _format_row(item: NewsItem, font_color: str) -> str:
    label = _SOURCE_LABELS.get(item.source, item.source)
    badge_color = _SOURCE_COLORS.get(item.source, "#888888")
    title_html = escape(item.title)
    if item.url:
        title_html = (
            f'<a href="{escape(item.url, quote=True)}" '
            f'style="color: {escape(font_color, quote=True)}; '
            f'text-decoration: underline">{title_html}</a>'
        )
    extras: list[str] = []
    if item.meta:
        extras.append(escape(item.meta))
    if item.description:
        extras.append(escape(item.description))
    extras_html = ""
    if extras:
        extras_html = (
            f'<div style="color:#bbbbbb; margin-left: 56px; '
            f'font-size: 11px">{" · ".join(extras)}</div>'
        )
    return (
        '<div style="margin: 4px 0">'
        f'<span style="color:{badge_color}; font-weight: bold">'
        f"[{escape(label)}]</span> "
        f"{title_html}"
        f"</div>{extras_html}"
    )
