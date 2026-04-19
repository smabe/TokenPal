"""Tests for the draggable chat-log divider."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from textual.containers import VerticalScroll

from tokenpal.ui.textual_overlay import (
    _CHAT_LOG_DEFAULT_WIDTH,
    _CHAT_LOG_MIN_WIDTH,
    DividerBar,
    TextualOverlay,
    TokenPalApp,
)


def _make_overlay(**cfg_overrides) -> TextualOverlay:
    config = {"buddy_name": "TestBuddy", "overlay": "textual"}
    config.update(cfg_overrides)
    ov = TextualOverlay(config)
    ov.setup()
    return ov


@pytest.fixture
def overlay() -> TextualOverlay:
    return _make_overlay()


@pytest.fixture
def app(overlay: TextualOverlay) -> TokenPalApp:
    assert overlay._app is not None
    return overlay._app


async def test_mount_uses_configured_chat_log_width() -> None:
    ov = _make_overlay(chat_log_width=55)
    app = ov._app
    assert app is not None
    async with app.run_test(size=(200, 40)):
        assert app._chat_log_width == 55
        chat_log = app.query_one("#chat-log", VerticalScroll)
        assert int(chat_log.styles.width.value) == 55


async def test_mount_clamps_stale_oversized_width() -> None:
    # Terminal too narrow to honor a stale 180-cell width; must clamp down.
    ov = _make_overlay(chat_log_width=180)
    app = ov._app
    assert app is not None
    async with app.run_test(size=(80, 30)):
        assert app._chat_log_width < 180
        assert app._chat_log_width >= _CHAT_LOG_MIN_WIDTH


async def test_clamp_floor_and_ceiling(app: TokenPalApp) -> None:
    async with app.run_test(size=(120, 30)):
        # Floor
        assert app._clamp_chat_log_width(0) == _CHAT_LOG_MIN_WIDTH
        assert app._clamp_chat_log_width(-500) == _CHAT_LOG_MIN_WIDTH
        # Ceiling: can't eat the buddy panel
        huge = app._clamp_chat_log_width(10_000)
        assert huge < 120
        assert huge <= 120 - app._buddy_min_width() - 1
        # Midrange passes through
        assert app._clamp_chat_log_width(50) == 50


async def test_drag_move_updates_width_and_persists_on_end(app: TokenPalApp) -> None:
    async with app.run_test(size=(200, 40)):
        app._chat_log_width = _CHAT_LOG_DEFAULT_WIDTH
        app._apply_chat_log_width()
        # Simulate drag: start -> move 10 cells left -> end (chat should grow by 10).
        # DragStart handler captures state; first DragMove anchors origin.
        app.on_divider_bar_drag_start(DividerBar.DragStart())
        app.on_divider_bar_drag_move(DividerBar.DragMove(screen_x=100))
        # First DragMove just anchors the origin; width unchanged.
        assert app._chat_log_width == _CHAT_LOG_DEFAULT_WIDTH
        # Second move: 10 cells to the left => chat_log grows by 10.
        app.on_divider_bar_drag_move(DividerBar.DragMove(screen_x=90))
        assert app._chat_log_width == _CHAT_LOG_DEFAULT_WIDTH + 10

        # End: persistence callback fires on the overlay.
        with patch(
            "tokenpal.config.ui_writer.set_chat_log_width",
            return_value=Path("/tmp/x.toml"),
        ) as m:
            app.on_divider_bar_drag_end(DividerBar.DragEnd())
            m.assert_called_once_with(_CHAT_LOG_DEFAULT_WIDTH + 10)


async def test_drag_move_respects_bounds(app: TokenPalApp) -> None:
    async with app.run_test(size=(120, 30)):
        app._chat_log_width = 40
        app._apply_chat_log_width()
        app.on_divider_bar_drag_start(DividerBar.DragStart())
        app.on_divider_bar_drag_move(DividerBar.DragMove(screen_x=100))
        # Drag far right => chat_log tries to shrink below floor.
        app.on_divider_bar_drag_move(DividerBar.DragMove(screen_x=500))
        assert app._chat_log_width == _CHAT_LOG_MIN_WIDTH
        # Drag far left => chat_log tries to grow past ceiling.
        app.on_divider_bar_drag_move(DividerBar.DragMove(screen_x=-500))
        ceiling = 120 - app._buddy_min_width() - 1
        assert app._chat_log_width == ceiling


async def test_toggle_preserves_drag_width(app: TokenPalApp) -> None:
    async with app.run_test(size=(200, 40)):
        app._chat_log_width = 72
        app._apply_chat_log_width()
        # Hide
        app.action_toggle_chat_log()
        chat_log = app.query_one("#chat-log", VerticalScroll)
        divider = app.query_one(DividerBar)
        assert chat_log.display is False
        assert divider.display is False
        # Show again — width should be restored to 72, not reset to default.
        app.action_toggle_chat_log()
        assert chat_log.display is True
        assert divider.display is True
        assert int(chat_log.styles.width.value) == 72
