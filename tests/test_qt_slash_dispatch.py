"""Slash-command dispatch in the Qt chat window.

Input lines starting with ``/`` must route to the command callback;
anything else must route to the input callback. Matches the Textual
overlay's behavior so slash commands keep working under the Qt shell.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from tokenpal.ui.qt.chat_window import ChatDock  # noqa: E402
from tokenpal.ui.qt.overlay import QtOverlay  # noqa: E402


@pytest.fixture
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


def test_slash_prefix_routes_to_command_callback(qapp: QApplication) -> None:
    overlay = QtOverlay(config={})
    overlay.setup()
    try:
        cmd: list[str] = []
        inp: list[str] = []
        overlay.set_command_callback(cmd.append)
        overlay.set_input_callback(inp.append)
        overlay._on_user_submit("/help")
        overlay._on_user_submit("hello buddy")
        overlay._on_user_submit("/voice list")
        assert cmd == ["/help", "/voice list"]
        assert inp == ["hello buddy"]
    finally:
        overlay.teardown()


def test_chat_dock_submit_handler_receives_raw_text(
    qapp: QApplication,
) -> None:
    """The ChatDock's on_submit hook must pass the raw user string
    through — the router lives in QtOverlay, not the dock."""
    received: list[str] = []
    dock = ChatDock(on_submit=received.append)
    try:
        dock._input.setText("/options")
        dock._submit()
        dock._input.setText("yo")
        dock._submit()
        assert received == ["/options", "yo"]
    finally:
        dock.close()
