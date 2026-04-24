"""ChatHistoryWindow color/opacity composition tests.

Exercises the pure setters so we don't need a live overlay. Verifies:
- set_background_color + set_background_opacity compose into one QBrush
  whose alpha reflects opacity and whose RGB reflects the picked color.
- set_font_color re-emits the QTextBrowser stylesheet without dropping
  the glass scrollbar rules or the transparent-background directive.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from tokenpal.ui.qt.chat_window import ChatHistoryWindow  # noqa: E402
from tokenpal.ui.qt.speech_bubble import (  # noqa: E402
    _BUBBLE_BG_ALPHA,
    SpeechBubble,
)


@pytest.fixture
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


def test_background_color_and_opacity_compose(qapp: QApplication) -> None:
    win = ChatHistoryWindow()
    win.set_background_color("#336699")
    win.set_background_opacity(0.5)
    color = win._background_brush.color()
    assert (color.red(), color.green(), color.blue()) == (0x33, 0x66, 0x99)
    # 0.5 * 255 = 127.5 → rounds to 128.
    assert color.alpha() == 128


def test_color_change_preserves_opacity_alpha(qapp: QApplication) -> None:
    win = ChatHistoryWindow()
    win.set_background_opacity(0.8)
    win.set_background_color("#112233")
    color = win._background_brush.color()
    assert color.alpha() == int(round(0.8 * 255))
    assert (color.red(), color.green(), color.blue()) == (0x11, 0x22, 0x33)


def test_invalid_background_color_falls_back_to_default(
    qapp: QApplication,
) -> None:
    win = ChatHistoryWindow()
    win.set_background_opacity(1.0)
    win.set_background_color("not a color")
    color = win._background_brush.color()
    # Default is #000000, opacity 1.0 → fully opaque black.
    assert (color.red(), color.green(), color.blue()) == (0, 0, 0)
    assert color.alpha() == 255


def test_font_color_applies_to_log_stylesheet(qapp: QApplication) -> None:
    win = ChatHistoryWindow()
    win.set_font_color("#abcdef")
    stylesheet = win._log.styleSheet()
    assert "color: #abcdef" in stylesheet
    assert "background: transparent" in stylesheet


def test_font_color_preserves_scrollbar_rules(qapp: QApplication) -> None:
    win = ChatHistoryWindow()
    before = win._log.styleSheet()
    # The glass scrollbar rules are appended at construction — grab a
    # stable substring that should survive every re-emit.
    assert "QScrollBar" in before
    win.set_font_color("#ff00ff")
    after = win._log.styleSheet()
    assert "QScrollBar" in after


def test_invalid_font_color_falls_back_to_default_white(
    qapp: QApplication,
) -> None:
    win = ChatHistoryWindow()
    win.set_font_color("garbage")
    assert "color: #ffffff" in win._log.styleSheet()


def test_bubble_set_background_color_keeps_fixed_alpha(
    qapp: QApplication,
) -> None:
    bubble = SpeechBubble()
    bubble.set_background_color("#445566")
    assert (
        bubble._bg_color.red(),
        bubble._bg_color.green(),
        bubble._bg_color.blue(),
    ) == (0x44, 0x55, 0x66)
    # Bubble alpha is decoupled from the chat_log opacity slider.
    assert bubble._bg_color.alpha() == _BUBBLE_BG_ALPHA


def test_bubble_set_font_color_normalizes(qapp: QApplication) -> None:
    bubble = SpeechBubble()
    bubble.set_font_color("#ABCDEF")
    assert (
        bubble._fg_color.red(),
        bubble._fg_color.green(),
        bubble._fg_color.blue(),
    ) == (0xAB, 0xCD, 0xEF)


def test_bubble_rejects_invalid_color_via_fallback(
    qapp: QApplication,
) -> None:
    bubble = SpeechBubble()
    bubble.set_background_color("nope")
    # Fallback is default black.
    assert (
        bubble._bg_color.red(),
        bubble._bg_color.green(),
        bubble._bg_color.blue(),
    ) == (0, 0, 0)
    assert bubble._bg_color.alpha() == _BUBBLE_BG_ALPHA
