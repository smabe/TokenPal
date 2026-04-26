"""Headless smoke tests for ``NewsHistoryWindow``."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from tokenpal.brain.news_buffer import NewsItem  # noqa: E402
from tokenpal.ui.qt.news_window import NewsHistoryWindow  # noqa: E402


@pytest.fixture
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _item(
    *,
    source: str = "lobsters",
    title: str = "Rust 2.0 released",
    url: str = "https://lobste.rs/s/abc",
    meta: str = "88 pts",
    description: str = "",
) -> NewsItem:
    return NewsItem(
        source=source, title=title, url=url, meta=meta,
        description=description, timestamp=0.0,
    )


def test_constructs_hidden(qapp: QApplication) -> None:
    win = NewsHistoryWindow()
    assert not win.isVisible()


def test_appends_item_with_source_badge_and_link(qapp: QApplication) -> None:
    win = NewsHistoryWindow()
    win.append_items([_item()])
    html = win._log.toHtml()
    assert "Lobsters" in html
    assert "Rust 2.0 released" in html
    assert "https://lobste.rs/s/abc" in html


def test_each_source_renders_distinct_label(qapp: QApplication) -> None:
    win = NewsHistoryWindow()
    win.append_items([
        _item(source="world_awareness", title="HN one", url="https://h/1"),
        _item(source="lobsters", title="Lob one", url="https://l/1"),
        _item(source="github_trending", title="foo/bar", url="https://g/1"),
    ])
    html = win._log.toHtml()
    assert "HN" in html
    assert "Lobsters" in html
    assert "GitHub" in html


def test_empty_state_replaced_on_first_append(qapp: QApplication) -> None:
    win = NewsHistoryWindow()
    assert "Headlines will land here" in win._log.toHtml()
    win.append_items([_item()])
    html = win._log.toHtml()
    assert "Headlines will land here" not in html


def test_item_without_url_renders_plain_title(qapp: QApplication) -> None:
    win = NewsHistoryWindow()
    win.append_items([_item(url="", title="Self post no link")])
    html = win._log.toHtml()
    assert "Self post no link" in html
    assert 'href="' not in html or "Self post no link" not in (
        html.split('href="', 1)[1] if 'href="' in html else ""
    )


def test_clear_resets_to_empty_state(qapp: QApplication) -> None:
    win = NewsHistoryWindow()
    win.append_items([_item()])
    win.clear()
    assert "Headlines will land here" in win._log.toHtml()


def test_meta_renders_as_inline_break_not_indented_block(
    qapp: QApplication,
) -> None:
    """The meta line must live inside the same paragraph as the title
    (separated by <br>) — sibling block elements with margin-left on
    the meta caused QTextBrowser to leak indent into subsequent rows.
    """
    from tokenpal.ui.qt.news_window import _format_row

    item = _item(meta="42 pts", description="hello")
    html = _format_row(item, font_color="#ffffff")
    assert "<br>" in html
    assert "margin-left" not in html
