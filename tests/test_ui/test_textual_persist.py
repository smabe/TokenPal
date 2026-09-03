"""The Textual overlay's unpersisted chat channel.

`log_buddy_message(..., persist=False)` must still render in the chat pane
while keeping the line out of the persisted chat log.
"""

from __future__ import annotations

import pytest

from tokenpal.ui.textual_overlay import TextualOverlay, TokenPalApp


@pytest.fixture
def overlay() -> TextualOverlay:
    ov = TextualOverlay({"buddy_name": "TestBuddy", "overlay": "textual"})
    ov.setup()
    return ov


@pytest.fixture
def app(overlay: TextualOverlay) -> TokenPalApp:
    assert overlay._app is not None
    return overlay._app


async def test_unpersisted_buddy_line_skips_persist_callback(
    overlay: TextualOverlay, app: TokenPalApp,
) -> None:
    persisted: list[str] = []
    overlay._chat_persist_callback = lambda _s, text, _u: persisted.append(text)

    async with app.run_test(size=(120, 40)):
        app._log_buddy("kept out", persist=False)

        assert any("kept out" in line for line in app._chat_log_lines)
        assert persisted == []


async def test_overlay_adapter_forwards_persist_through_the_message(
    overlay: TextualOverlay, app: TokenPalApp,
) -> None:
    """`on_log_buddy_message` must carry `persist` across the Message hop —
    dropping it would silently re-persist."""
    persisted: list[str] = []
    overlay._chat_persist_callback = lambda _s, text, _u: persisted.append(text)

    async with app.run_test(size=(120, 40)) as pilot:
        overlay.log_buddy_message("kept out", persist=False)
        overlay.log_buddy_message("kept in")
        await pilot.pause()

        assert any("kept out" in line for line in app._chat_log_lines)
        assert persisted == ["kept in"]


async def test_persisted_buddy_line_reaches_the_callback(
    overlay: TextualOverlay, app: TokenPalApp,
) -> None:
    persisted: list[str] = []
    overlay._chat_persist_callback = lambda _s, text, _u: persisted.append(text)

    async with app.run_test(size=(120, 40)):
        app._log_buddy("kept in")

        assert persisted == ["kept in"]
