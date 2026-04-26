"""News history window — frameless translucent log of every world-news
headline the buddy has picked up this session. Same chrome as
``ChatHistoryWindow`` (shared base in ``_log_window.py``); one row
per headline with source badge, clickable link, and small meta line.
"""

from __future__ import annotations

from collections.abc import Callable
from html import escape

from tokenpal.brain.news_buffer import NewsItem
from tokenpal.ui.qt._log_window import TranslucentLogWindow

_DEFAULT_SIZE = (520, 420)
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


class NewsHistoryWindow(TranslucentLogWindow):
    """Frameless translucent scrollable list of news headlines.
    Starts hidden — the tray "Show news" toggle reveals it.
    """

    LOG_MAX_LINES = 600

    def __init__(
        self,
        *,
        title: str = "News",
        on_hide: Callable[[], None] | None = None,
        on_zoom: Callable[[int], None] | None = None,
    ) -> None:
        super().__init__(
            title=title,
            default_size=_DEFAULT_SIZE,
            on_hide=on_hide,
            on_zoom=on_zoom,
        )
        self._empty_state_shown = False
        self._show_empty_state()

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
            f'<br><span style="color:#bbbbbb; font-size: small">'
            f'{" · ".join(extras)}</span>'
        )
    # Single block per item: QTextBrowser appends each call as a paragraph,
    # and CSS block-margin tricks (margin-left on a child div) leak indent
    # into subsequent paragraphs. A flat span + <br> renders cleanly.
    return (
        '<p style="margin: 4px 0">'
        f'<span style="color:{badge_color}; font-weight: bold">'
        f"[{escape(label)}]</span> "
        f"{title_html}"
        f"{extras_html}"
        "</p>"
    )
