"""Tests for the Textual overlay using Textual's Pilot test harness."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from textual.widgets import Input

from tokenpal.ui.ascii_renderer import BuddyFrame, SpeechBubble
from tokenpal.ui.textual_overlay import (
    BuddyWidget,
    HeaderWidget,
    HideSpeech,
    ShowBuddy,
    ShowSpeech,
    SpeechBubbleWidget,
    StatusBarWidget,
    TextualOverlay,
    TokenPalApp,
    UpdateStatus,
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


async def test_header_renders_buddy_name(app: TokenPalApp) -> None:
    async with app.run_test():
        header = app.query_one(HeaderWidget)
        assert "TestBuddy" in header.render().plain


async def test_buddy_shows_idle_on_mount(app: TokenPalApp) -> None:
    async with app.run_test():
        buddy = app.query_one(BuddyWidget)
        assert "Ө" in buddy.render().plain


async def test_buddy_updates_via_message(app: TokenPalApp) -> None:
    async with app.run_test() as pilot:
        app.post_message(ShowBuddy(BuddyFrame.get("talking")))
        await pilot.pause()
        buddy = app.query_one(BuddyWidget)
        assert "◇" in buddy.render().plain


async def test_speech_bubble_starts_via_message(app: TokenPalApp) -> None:
    async with app.run_test(size=(80, 24)) as pilot:
        speech = app.query_one(SpeechBubbleWidget)
        assert speech.display is False

        app.post_message(ShowSpeech(SpeechBubble(text="Hello")))
        await pilot.pause()
        assert speech.display is True


async def test_speech_bubble_typing_completes(app: TokenPalApp) -> None:
    async with app.run_test(size=(80, 24)) as pilot:
        speech = app.query_one(SpeechBubbleWidget)
        app.post_message(ShowSpeech(SpeechBubble(text="Hi")))
        await pilot.pause()

        # Advance past typing (2 chars * 30ms + margin)
        await asyncio.sleep(0.15)
        await pilot.pause()

        assert "Hi" in speech.render().plain


async def test_speech_bubble_hide_via_message(app: TokenPalApp) -> None:
    async with app.run_test(size=(80, 24)) as pilot:
        speech = app.query_one(SpeechBubbleWidget)
        app.post_message(ShowSpeech(SpeechBubble(text="hi")))
        await pilot.pause()
        assert speech.display is True

        app.post_message(HideSpeech())
        await pilot.pause()
        assert speech.display is False


async def test_status_bar_via_message(app: TokenPalApp) -> None:
    async with app.run_test() as pilot:
        app.post_message(UpdateStatus("snarky | Ghostty | 54F clear"))
        await pilot.pause()
        bar = app.query_one(StatusBarWidget)
        assert "snarky" in bar.render().plain


async def test_input_dispatches_text(
    app: TokenPalApp, overlay: TextualOverlay,
) -> None:
    cb = MagicMock()
    overlay.set_input_callback(cb)

    async with app.run_test() as pilot:
        inp = app.query_one("#user-input", expect_type=Input)
        inp.value = "hello buddy"
        await inp.action_submit()
        await pilot.pause()
        cb.assert_called_once_with("hello buddy")


async def test_input_dispatches_command(
    app: TokenPalApp, overlay: TextualOverlay,
) -> None:
    cb = MagicMock()
    overlay.set_command_callback(cb)

    async with app.run_test() as pilot:
        inp = app.query_one("#user-input", expect_type=Input)
        inp.value = "/help"
        await inp.action_submit()
        await pilot.pause()
        cb.assert_called_once_with("/help")


async def test_input_clears_after_submit(app: TokenPalApp) -> None:
    async with app.run_test() as pilot:
        inp = app.query_one("#user-input", expect_type=Input)
        inp.value = "test"
        await inp.action_submit()
        await pilot.pause()
        assert inp.value == ""


async def test_empty_input_not_dispatched(
    app: TokenPalApp, overlay: TextualOverlay,
) -> None:
    cb = MagicMock()
    overlay.set_input_callback(cb)

    async with app.run_test() as pilot:
        inp = app.query_one("#user-input", expect_type=Input)
        inp.value = "   "
        await inp.action_submit()
        await pilot.pause()
        cb.assert_not_called()


async def test_persistent_bubble_no_auto_hide(app: TokenPalApp) -> None:
    async with app.run_test(size=(80, 24)) as pilot:
        speech = app.query_one(SpeechBubbleWidget)
        app.post_message(ShowSpeech(SpeechBubble(text="Loading...", persistent=True)))
        await pilot.pause()

        # Let typing complete
        await asyncio.sleep(0.4)
        await pilot.pause()

        assert speech._hide_timer is None
        assert speech.display is True


async def test_overlay_post_is_thread_safe(
    app: TokenPalApp, overlay: TextualOverlay,
) -> None:
    """Verify post_message works from a background thread."""
    async with app.run_test(size=(80, 24)) as pilot:
        overlay._is_running = True
        bubble = SpeechBubble(text="from thread")

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, overlay.show_speech, bubble)
        await pilot.pause()

        speech = app.query_one(SpeechBubbleWidget)
        assert speech.display is True
