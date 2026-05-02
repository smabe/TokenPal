"""Speech bubble follows the buddy as he swings.

When the physics tick moves BuddyWindow, the overlay must reposition
the bubble so it stays attached to the buddy's head instead of
remaining frozen at its first-render spot.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tokenpal.ui.ascii_renderer import SpeechBubble  # noqa: E402
from tokenpal.ui.qt.overlay import QtOverlay  # noqa: E402


@pytest.fixture
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


def _pump(qapp: QApplication, ms: int = 30) -> None:
    QTimer.singleShot(ms, qapp.quit)
    qapp.exec()


def test_bubble_repositions_when_buddy_moves(qapp: QApplication) -> None:
    overlay = QtOverlay(config={})
    overlay.setup()
    try:
        overlay.show_speech(SpeechBubble("hello"))
        _pump(qapp, ms=50)
        assert overlay._bubble is not None
        assert overlay._buddy is not None
        before = (overlay._bubble.x(), overlay._bubble.y())

        # Nudge the buddy sideways and let the physics settle long
        # enough for the move to propagate to the bubble via the
        # position_changed signal.
        overlay._buddy.sim.snap_home(900.0, 500.0)
        overlay._buddy.wake_tick_timer()
        _pump(qapp, ms=120)

        after = (overlay._bubble.x(), overlay._bubble.y())
        assert after != before, (
            "bubble should have followed the buddy across the screen"
        )
    finally:
        overlay.teardown()
        _pump(qapp, ms=20)


def test_buddy_emits_position_changed_while_awake(qapp: QApplication) -> None:
    from tokenpal.ui.ascii_renderer import BUDDY_IDLE
    from tokenpal.ui.qt.buddy_window import BuddyWindow
    buddy = BuddyWindow(
        frame_lines=BUDDY_IDLE, initial_anchor=(300.0, 300.0),
    )
    try:
        fires: list[int] = []
        buddy.position_changed.connect(lambda: fires.append(1))
        # Wake with an impulse so the sim has motion to advance —
        # post-moveo-it, position_changed only fires while the buddy is
        # awake (not sleeping AND not dragging AND no rescue pending).
        buddy.sim.apply_impulse(0.0, -200.0)
        buddy.core._on_tick()
        assert fires, "position_changed should fire while buddy is awake"
    finally:
        buddy.close()


def test_buddy_skips_emit_when_idle(qapp: QApplication) -> None:
    from tokenpal.ui.ascii_renderer import BUDDY_IDLE
    from tokenpal.ui.qt.buddy_window import BuddyWindow
    buddy = BuddyWindow(
        frame_lines=BUDDY_IDLE, initial_anchor=(300.0, 300.0),
    )
    try:
        # Drive to fully-settled state.
        buddy.sim.snap_home(300.0, 300.0)
        buddy.core._on_tick()
        fires: list[int] = []
        buddy.position_changed.connect(lambda: fires.append(1))
        for _ in range(10):
            buddy.core._on_tick()
        assert fires == [], (
            "position_changed should not fire on subsequent ticks after the "
            "buddy has settled"
        )
    finally:
        buddy.close()
