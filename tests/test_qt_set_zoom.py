"""Phase 4 of size-me-up: ``set_zoom(factor)`` on BuddyWindow,
SpeechBubble, and ChatDock. Each rescales font + geometry; BuddyWindow
also recomputes the rigid-body inertia so the simulator tracks the new
bounding box (failure mode flagged in plans/size-me-up.md).
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tokenpal.ui.ascii_renderer import BUDDY_IDLE  # noqa: E402
from tokenpal.ui.qt.buddy_window import BuddyWindow  # noqa: E402
from tokenpal.ui.qt.chat_window import ChatDock  # noqa: E402
from tokenpal.ui.qt.speech_bubble import SpeechBubble  # noqa: E402


@pytest.fixture
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


def test_buddy_set_zoom_rescales_font_cells_and_inertia(
    qapp: QApplication,
) -> None:
    buddy = BuddyWindow(
        frame_lines=BUDDY_IDLE, initial_anchor=(300.0, 300.0), font_size=14,
    )
    try:
        base_size = buddy._font.pointSize()
        base_cell_w = buddy._cell_w
        base_line_h = buddy._line_h
        base_art_w = buddy._art_w
        base_art_h = buddy._art_h
        base_inertia = buddy._sim.config.inertia

        buddy.set_zoom(2.0)

        assert buddy._font.pointSize() == base_size * 2
        assert buddy._cell_w >= base_cell_w
        assert buddy._line_h > base_line_h
        assert buddy._art_w > base_art_w
        assert buddy._art_h > base_art_h
        # Inertia is mass × R²/2 with R derived from art bounds, so a
        # ~2× linear scale should land inertia near 4× the baseline.
        assert buddy._sim.config.inertia > base_inertia * 2.5
    finally:
        buddy.close()


def test_buddy_set_zoom_noop_for_same_factor_and_rejects_zero(
    qapp: QApplication,
) -> None:
    buddy = BuddyWindow(
        frame_lines=BUDDY_IDLE, initial_anchor=(300.0, 300.0),
    )
    try:
        before = (buddy._font.pointSize(), buddy._sim.config.inertia)
        buddy.set_zoom(1.0)
        assert (buddy._font.pointSize(), buddy._sim.config.inertia) == before
        buddy.set_zoom(0.0)
        assert (buddy._font.pointSize(), buddy._sim.config.inertia) == before
        buddy.set_zoom(-1.5)
        assert (buddy._font.pointSize(), buddy._sim.config.inertia) == before
    finally:
        buddy.close()


def test_buddy_set_zoom_chains_from_base_not_current(
    qapp: QApplication,
) -> None:
    """Two consecutive set_zoom calls must compose against the base
    font size, not the previously-zoomed size — otherwise zoom drifts
    multiplicatively across drag updates."""
    buddy = BuddyWindow(
        frame_lines=BUDDY_IDLE, initial_anchor=(300.0, 300.0), font_size=14,
    )
    try:
        buddy.set_zoom(2.0)
        size_at_2x = buddy._font.pointSize()
        buddy.set_zoom(1.0)
        assert buddy._font.pointSize() == 14
        buddy.set_zoom(2.0)
        assert buddy._font.pointSize() == size_at_2x
    finally:
        buddy.close()


def test_bubble_set_zoom_grows_font_and_content(qapp: QApplication) -> None:
    bubble = SpeechBubble(font_size=13)
    try:
        bubble.show_text("hello world", typing=False)
        base_size = bubble._font.pointSize()
        base_w = bubble._content_w
        base_h = bubble._content_h

        bubble.set_zoom(2.0)

        assert bubble._font.pointSize() == base_size * 2
        assert bubble._content_w > base_w
        assert bubble._content_h > base_h
    finally:
        bubble.hide_bubble()


def test_bubble_set_zoom_chains_from_base(qapp: QApplication) -> None:
    bubble = SpeechBubble(font_size=13)
    try:
        bubble.set_zoom(2.0)
        bubble.set_zoom(1.0)
        assert bubble._font.pointSize() == 13
    finally:
        bubble.hide_bubble()


def test_dock_set_zoom_scales_width_and_font(qapp: QApplication) -> None:
    dock = ChatDock()
    try:
        # apply_font seeds the base font from which set_zoom derives the
        # effective font size.
        base_font = QFont("Courier", 12)
        dock.apply_font(base_font)
        base_w = dock.width()
        base_input_size = dock._input.font().pointSize()

        dock.set_zoom(2.0)

        assert dock.width() >= base_w * 2 - 2
        assert dock._input.font().pointSize() == base_input_size * 2
        assert dock._input.height() >= 60
    finally:
        dock.close()


def test_dock_set_zoom_chains_from_base(qapp: QApplication) -> None:
    dock = ChatDock()
    try:
        dock.apply_font(QFont("Courier", 12))
        dock.set_zoom(2.0)
        dock.set_zoom(1.0)
        assert dock._input.font().pointSize() == 12
    finally:
        dock.close()
