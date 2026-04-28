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


def test_buddy_set_zoom_scales_force_magnitude_physics(
    qapp: QApplication,
) -> None:
    """max_linear_speed, upright_bias, and settle thresholds rescale
    on zoom; gravity does NOT (drag-zoom can't land exactly on 1.0,
    so scaling g would leave it off-base every time the buddy is
    visually back to normal size)."""
    buddy = BuddyWindow(
        frame_lines=BUDDY_IDLE, initial_anchor=(300.0, 300.0), font_size=14,
    )
    try:
        cfg_1x = buddy._sim.config
        buddy.set_zoom(2.0)
        cfg_2x = buddy._sim.config
        assert cfg_2x.gravity == cfg_1x.gravity
        assert cfg_2x.max_linear_speed == cfg_1x.max_linear_speed * 2.0
        assert cfg_2x.upright_bias_strength == cfg_1x.upright_bias_strength * 4.0
        assert cfg_2x.upright_bias_radius == cfg_1x.upright_bias_radius * 2.0
        assert cfg_2x.settle_speed == cfg_1x.settle_speed * 2.0
        assert cfg_2x.settle_distance == cfg_1x.settle_distance * 2.0
        # Scale-free quantities stay put.
        assert cfg_2x.home_frequency_hz == cfg_1x.home_frequency_hz
        assert cfg_2x.grab_frequency_hz == cfg_1x.grab_frequency_hz
        assert cfg_2x.max_angular_speed == cfg_1x.max_angular_speed
        assert cfg_2x.mass == cfg_1x.mass
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


# --- QtOverlay orchestrator -------------------------------------------------
from tokenpal.ui.qt.overlay import QtOverlay  # noqa: E402


def test_overlay_set_zoom_clamps_and_fans_out(qapp: QApplication) -> None:
    overlay = QtOverlay(config={})
    overlay.setup()
    try:
        overlay.set_zoom(5.0)  # above max
        assert overlay._zoom == 2.5
        assert overlay._buddy is not None and overlay._buddy._zoom == 2.5
        assert overlay._bubble is not None and overlay._bubble._zoom == 2.5

        overlay.set_zoom(0.1)  # below min
        assert overlay._zoom == 0.5
        assert overlay._buddy._zoom == 0.5
        assert overlay._dock is not None and overlay._dock._zoom == 0.5
    finally:
        overlay.teardown()


def test_overlay_set_zoom_persists(qapp: QApplication) -> None:
    overlay = QtOverlay(config={})
    overlay.setup()
    saved: list[dict] = []
    overlay.set_ui_state_persist_callback(saved.append)
    try:
        overlay.set_zoom(1.5)
        overlay.flush_pending_persist()
        assert saved, "set_zoom should fire the persist callback"
        assert saved[-1]["zoom"] == 1.5
    finally:
        overlay.teardown()


def test_overlay_set_zoom_noop_short_circuits_persist(
    qapp: QApplication,
) -> None:
    overlay = QtOverlay(config={})
    overlay.setup()
    saved: list[dict] = []
    overlay.set_ui_state_persist_callback(saved.append)
    try:
        overlay.set_zoom(1.5)
        overlay.flush_pending_persist()
        baseline = len(saved)
        overlay.set_zoom(1.5)
        overlay.set_zoom(1.50001)  # snaps to 1.5 at 4dp
        overlay.flush_pending_persist()
        assert len(saved) == baseline, (
            "no-op zoom changes must not enqueue a persist"
        )
    finally:
        overlay.teardown()


def test_overlay_restore_visibility_state_applies_zoom_at_setup(
    qapp: QApplication,
) -> None:
    overlay = QtOverlay(config={})
    overlay.restore_visibility_state(buddy_visible=True, zoom=1.75)
    overlay.setup()
    try:
        assert overlay._zoom == 1.75
        assert overlay._buddy is not None and overlay._buddy._zoom == 1.75
        assert overlay._bubble is not None and overlay._bubble._zoom == 1.75
        assert overlay._dock is not None and overlay._dock._zoom == 1.75
    finally:
        overlay.teardown()


def test_overlay_persist_includes_zoom_on_visibility_toggle(
    qapp: QApplication,
) -> None:
    """Latent bug from phase-0 callback: a visibility toggle must NOT
    silently clobber a non-default zoom on disk."""
    overlay = QtOverlay(config={})
    overlay.setup()
    saved: list[dict] = []
    overlay.set_ui_state_persist_callback(saved.append)
    try:
        overlay.set_zoom(1.5)
        overlay._do_toggle_window("news")
        overlay.flush_pending_persist()
        assert saved[-1]["zoom"] == 1.5, (
            "visibility toggle must preserve zoom across persist"
        )
    finally:
        overlay.teardown()


def test_buddy_zoom_drag_delta_signal_drives_overlay(
    qapp: QApplication,
) -> None:
    overlay = QtOverlay(config={})
    overlay.setup()
    try:
        assert overlay._resize_grip is not None
        # _ZOOM_PER_DRAG_PX = 0.005 → 100 px drag = +0.5 zoom delta
        # from 1.0 → 1.5.
        overlay._resize_grip.zoom_drag_delta.emit(100)
        assert overlay._zoom == 1.5
    finally:
        overlay.teardown()


def test_resize_grip_pose_tracks_buddy_rotation(qapp: QApplication) -> None:
    """The grip is a top-level widget pinned to the buddy's body-frame
    bottom-right. Rotating the buddy must change the grip's painted
    angle so the dots stay glued to the body during a swing."""
    import math

    overlay = QtOverlay(config={})
    overlay.setup()
    try:
        assert overlay._buddy is not None
        assert overlay._resize_grip is not None
        # Reposition once at theta=0 to capture the upright pose.
        overlay._reposition_grip()
        upright_angle = overlay._resize_grip._angle_rad
        upright_pos = overlay._resize_grip.pos()

        # Force a non-zero theta on the simulator and re-pose.
        overlay._buddy._sim._theta = math.radians(30.0)
        overlay._reposition_grip()

        assert overlay._resize_grip._angle_rad != upright_angle, (
            "grip angle should track buddy.body_angle()"
        )
        assert overlay._resize_grip.pos() != upright_pos, (
            "grip position should track the rotated body-frame corner"
        )
    finally:
        overlay.teardown()
