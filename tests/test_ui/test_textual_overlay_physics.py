"""Physics / mouse-interaction tests for the buddy stage.

Covers click vs drag disambiguation, shake routing, mouse-capture release
on modals, and the overlay→brain reaction-callback bridge. Motion state
decay is covered in ``tests/test_buddy_environment.py``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from textual.events import MouseDown, MouseMove, MouseUp

from tokenpal.ui.buddy_environment import BuddyMotion
from tokenpal.ui.confirm_modal import ConfirmModal
from tokenpal.ui.textual_overlay import (
    BuddyStage,
    TextualOverlay,
    TokenPalApp,
)


@pytest.fixture
def overlay() -> TextualOverlay:
    config = {"buddy_name": "TestBuddy", "overlay": "textual"}
    ov = TextualOverlay(config)
    ov.setup()
    return ov


@pytest.fixture
def app(overlay: TextualOverlay) -> TokenPalApp:
    assert overlay._app is not None
    return overlay._app


def _mouse_down(stage: BuddyStage, x: int, y: int) -> None:
    # Bypass Textual's event plumbing by calling the handler directly with a
    # minimally-populated MouseDown. The handler only reads screen_x/screen_y.
    event = MagicMock(spec=MouseDown)
    event.screen_x = x
    event.screen_y = y
    stage.on_mouse_down(event)


def _mouse_move(stage: BuddyStage, x: int, y: int) -> None:
    event = MagicMock(spec=MouseMove)
    event.screen_x = x
    event.screen_y = y
    stage.on_mouse_move(event)


def _mouse_up(stage: BuddyStage, x: int, y: int) -> None:
    event = MagicMock(spec=MouseUp)
    event.screen_x = x
    event.screen_y = y
    stage.on_mouse_up(event)


async def test_stage_mounts_and_binds_motion(app: TokenPalApp) -> None:
    async with app.run_test():
        stage = app.query_one(BuddyStage)
        motion = stage._motion()
        assert isinstance(motion, BuddyMotion)


async def test_click_posts_buddy_poked_message(
    app: TokenPalApp, overlay: TextualOverlay,
) -> None:
    cb = MagicMock()
    overlay.set_buddy_reaction_callback(cb)
    async with app.run_test() as pilot:
        stage = app.query_one(BuddyStage)
        _mouse_down(stage, 10, 5)
        _mouse_up(stage, 10, 5)
        await pilot.pause()
    cb.assert_called_once_with("poke")


async def test_drag_does_not_fire_poke(
    app: TokenPalApp, overlay: TextualOverlay,
) -> None:
    cb = MagicMock()
    overlay.set_buddy_reaction_callback(cb)
    async with app.run_test() as pilot:
        stage = app.query_one(BuddyStage)
        _mouse_down(stage, 10, 5)
        # Move past the 4-cell threshold to promote to drag.
        for x in range(11, 20):
            _mouse_move(stage, x, 5)
        _mouse_up(stage, 20, 5)
        await pilot.pause()
    cb.assert_not_called()


async def test_drag_writes_stage_offset(app: TokenPalApp) -> None:
    async with app.run_test() as pilot:
        stage = app.query_one(BuddyStage)
        _mouse_down(stage, 10, 5)
        for x in range(11, 20):
            _mouse_move(stage, x, 5)
        await pilot.pause()
        # Drag moved +9 cells right. Stage offset tracks drag_offset_x.
        offset = stage.styles.offset
        assert int(offset.x.value) > 0


async def test_force_release_clears_capture_state(app: TokenPalApp) -> None:
    async with app.run_test():
        stage = app.query_one(BuddyStage)
        _mouse_down(stage, 10, 5)
        assert stage._mouse_down_at is not None
        stage.force_release()
        assert stage._mouse_down_at is None
        assert stage._dragging is False


async def test_modal_push_force_releases_stage(app: TokenPalApp) -> None:
    async with app.run_test() as pilot:
        stage = app.query_one(BuddyStage)
        _mouse_down(stage, 10, 5)
        # Start a drag so force_release has work to do.
        for x in range(11, 20):
            _mouse_move(stage, x, 5)
        assert stage._dragging is True

        modal = ConfirmModal(title="Test", body="?")
        app.push_screen(modal)
        await pilot.pause()
        assert stage._mouse_down_at is None
        assert stage._dragging is False


async def test_shake_posts_buddy_shaken(
    app: TokenPalApp, overlay: TextualOverlay,
) -> None:
    cb = MagicMock()
    overlay.set_buddy_reaction_callback(cb)
    async with app.run_test() as pilot:
        stage = app.query_one(BuddyStage)
        # Drive the motion directly — the shake-detection unit path is in
        # test_buddy_environment. Here we just verify the stage consumes the
        # trigger and forwards a BuddyShaken message to the callback.
        motion = stage._motion()
        assert motion is not None
        for dx in (3.0, -3.0, 3.0, -3.0):
            motion.drag_update(dx, 0.0, 0.05)
        assert motion.dizzy_ticks > 0.0
        # Simulate the stage noticing the trigger on the next drag_update.
        _mouse_down(stage, 10, 5)
        _mouse_move(stage, 14, 5)
        _mouse_move(stage, 18, 5)
        _mouse_up(stage, 18, 5)
        await pilot.pause()
    # Shake already fired before the mouse sequence; mouse_move consumed the
    # trigger and posted BuddyShaken.
    assert any(call.args == ("shake",) for call in cb.call_args_list)


async def test_buddy_widget_renders_particles_in_its_region(
    app: TokenPalApp,
) -> None:
    """Drop a particle at the buddy's panel-y and verify its glyph lands in
    BuddyWidget.render_line output (overlay on a blank cell)."""
    from tokenpal.ui.textual_overlay import BuddyWidget

    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        buddy = app.query_one(BuddyWidget)
        panel = buddy.parent
        assert panel is not None
        buddy_y_offset = buddy.region.y - panel.region.y
        # Spawn a particle at the buddy's first row. Glyph is a distinctive
        # character unlikely to appear in the ASCII art.
        field = app.env_controller.field
        from tokenpal.ui.buddy_environment import Particle
        field.particles.append(Particle(
            x=0.5, y=float(buddy_y_offset) + 0.0,
            vx=0.0, vy=0.0, ax=0.0, ay=0.0,
            life=99.0, glyph="§", color="#ff00ff",
        ))
        # Force a re-render and inspect row 0 of the buddy widget.
        buddy.refresh()
        await pilot.pause()
        strip = buddy.render_line(0)
        text = "".join(seg.text for seg in strip)
        assert "§" in text
