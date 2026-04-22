"""Chat dock follows the buddy as he swings.

Mirror of ``test_qt_bubble_follow`` — the dock's input + status strip
must re-anchor on every ``position_changed`` emission so it reads as
"glued to the buddy's feet" instead of stranded at first-render spot.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tokenpal.ui.qt.overlay import QtOverlay  # noqa: E402


@pytest.fixture
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


def _pump(qapp: QApplication, ms: int = 30) -> None:
    QTimer.singleShot(ms, qapp.quit)
    qapp.exec()


def test_dock_repositions_when_buddy_moves(qapp: QApplication) -> None:
    overlay = QtOverlay(config={})
    overlay.setup()
    try:
        assert overlay._dock is not None
        assert overlay._buddy is not None
        overlay._reposition_dock()
        _pump(qapp, ms=30)
        before = (overlay._dock.x(), overlay._dock.y())

        overlay._buddy._sim.set_anchor(1100.0, 600.0)
        overlay._buddy._wake_timer()
        _pump(qapp, ms=150)

        after = (overlay._dock.x(), overlay._dock.y())
        assert after != before, (
            "dock should follow the buddy via the position_changed signal"
        )
    finally:
        overlay.teardown()
        _pump(qapp, ms=20)


def test_dock_sits_below_buddy(qapp: QApplication) -> None:
    overlay = QtOverlay(config={})
    overlay.setup()
    try:
        assert overlay._dock is not None
        assert overlay._buddy is not None
        buddy_geom = overlay._buddy.geometry()
        overlay._reposition_dock()
        _pump(qapp, ms=20)
        # Dock's top must be at or below the buddy's bottom.
        assert overlay._dock.y() >= buddy_geom.bottom(), (
            "dock should hang below the buddy, not cover its feet"
        )
    finally:
        overlay.teardown()
        _pump(qapp, ms=20)


def test_dock_placement_follows_buddy_and_history_state(
    qapp: QApplication,
) -> None:
    """Dock placement is a function of (buddy_visible, history_visible).

    - buddy shown: dock floats (under the buddy)
    - buddy hidden, history shown: dock embedded in history
    - both hidden: dock hidden entirely

    Toggling one window must not change the other's visibility.
    """
    overlay = QtOverlay(config={})
    overlay.setup()
    try:
        assert overlay._dock is not None
        assert overlay._history is not None
        assert not overlay._dock_docked
        assert not overlay._history_user_visible

        # Buddy hidden + history still hidden → dock hides (state D).
        overlay._buddy_user_visible = False
        overlay._update_dock_placement()
        assert not overlay._dock_docked
        assert not overlay._history_user_visible, (
            "hiding buddy must not force history open"
        )

        # User opens history → dock embeds.
        overlay._do_toggle_chat()
        assert overlay._history_user_visible
        assert overlay._dock_docked, "dock should embed when buddy hidden + history shown"
        assert overlay._dock.parent() is overlay._history

        # User re-shows buddy → dock floats back.
        overlay._buddy_user_visible = True
        overlay._update_dock_placement()
        assert not overlay._dock_docked
        assert overlay._dock.parent() is None
        assert overlay._history_user_visible, (
            "showing buddy must not close history"
        )
    finally:
        overlay.teardown()
        _pump(qapp, ms=20)
