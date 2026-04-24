"""Edge-dock behavior for the Qt buddy.

Dropping the buddy near a screen edge snaps the anchor to the edge,
so he feels "sticky" to monitor boundaries. Multi-monitor handled by
looking up the screen under the anchor.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from tokenpal.ui.ascii_renderer import BUDDY_IDLE  # noqa: E402
from tokenpal.ui.qt.buddy_window import _EDGE_DOCK_THRESHOLD, BuddyWindow  # noqa: E402


@pytest.fixture
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


def _screen_geom(qapp: QApplication) -> tuple[int, int, int, int]:
    screen = qapp.primaryScreen()
    assert screen is not None
    g = screen.availableGeometry()
    return g.left(), g.top(), g.right(), g.bottom()


def test_dropping_near_left_edge_snaps_to_left(qapp: QApplication) -> None:
    left, top, _right, _bottom = _screen_geom(qapp)
    buddy = BuddyWindow(frame_lines=BUDDY_IDLE, initial_anchor=(float(left), 400.0))
    try:
        buddy._sim.set_pivot(float(left + _EDGE_DOCK_THRESHOLD - 5), 400.0)
        buddy._maybe_edge_dock()
        assert buddy._sim.pivot[0] == float(left)
    finally:
        buddy.close()


def test_dropping_near_top_edge_snaps_to_top(qapp: QApplication) -> None:
    left, top, _right, _bottom = _screen_geom(qapp)
    buddy = BuddyWindow(
        frame_lines=BUDDY_IDLE,
        initial_anchor=(float(left + 300), float(top)),
    )
    try:
        buddy._sim.set_pivot(
            float(left + 300), float(top + _EDGE_DOCK_THRESHOLD - 5),
        )
        buddy._maybe_edge_dock()
        assert buddy._sim.pivot[1] == float(top)
    finally:
        buddy.close()


def test_dropping_far_from_edge_does_not_snap(qapp: QApplication) -> None:
    left, top, right, bottom = _screen_geom(qapp)
    mid_x = float((left + right) / 2)
    mid_y = float((top + bottom) / 2)
    buddy = BuddyWindow(frame_lines=BUDDY_IDLE, initial_anchor=(mid_x, mid_y))
    try:
        buddy._sim.set_pivot(mid_x, mid_y)
        buddy._maybe_edge_dock()
        assert buddy._sim.pivot == (mid_x, mid_y)
    finally:
        buddy.close()
