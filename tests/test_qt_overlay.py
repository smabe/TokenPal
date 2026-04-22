"""Integration smoke test for QtOverlay.

Boots the overlay, calls every brain-invoked method with realistic
arguments, pumps the Qt event loop briefly so queued signals flush,
then tears down. Proves the full adapter surface works end-to-end on
this host before the app.py wiring lands (that's Phase 6).

Skipped when PySide6 isn't installed.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tokenpal.ui.ascii_renderer import BuddyFrame, SpeechBubble  # noqa: E402
from tokenpal.ui.buddy_environment import EnvironmentSnapshot  # noqa: E402
from tokenpal.ui.qt.overlay import QtOverlay  # noqa: E402


@pytest.fixture
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


def _pump(qapp: QApplication, ms: int = 30) -> None:
    QTimer.singleShot(ms, qapp.quit)
    qapp.exec()


def test_qt_overlay_full_adapter_surface(qapp: QApplication) -> None:
    overlay = QtOverlay(config={"buddy_name": "TokenPal", "font_size": 13})
    overlay.setup()
    try:
        # Lifecycle + frame.
        overlay.show_buddy(BuddyFrame.get("idle"))

        # Speech bubble lifecycle.
        overlay.show_speech(SpeechBubble("hello world"))
        overlay.hide_speech()

        # Chat log.
        overlay.log_user_message("hi buddy")
        overlay.log_buddy_message("hey you", markup=False, url=None)
        overlay.log_buddy_message(
            "click here", markup=False, url="https://example.com",
        )
        overlay.load_chat_history([
            (1_700_000_000.0, "you", "warm-up", None),
            (1_700_000_001.0, "buddy", "yep", None),
        ])
        overlay.update_status(
            "mood: sleepy | model: gemma4 | spoke 3s ago",
        )

        # Voice / mood.
        overlay.set_mood("sleepy")
        overlay.set_voice_name("TestBuddy")
        overlay.load_voice_frames({"idle": BuddyFrame.get("idle")}, None)
        overlay.clear_voice_frames()

        # Chat-pane control.
        overlay.toggle_chat_log()  # show
        overlay.toggle_chat_log()  # hide again

        # Environment provider — a real no-op here since the renderer
        # doesn't consume it yet, but we verify it doesn't crash.
        def _provider() -> EnvironmentSnapshot:
            return EnvironmentSnapshot(
                weather_data=None, idle_event=None, sensitive_suppressed=False,
            )
        overlay.set_environment_provider(_provider)

        # Callback wiring.
        received: list[str] = []
        overlay.set_input_callback(lambda s: received.append(f"input:{s}"))
        overlay.set_command_callback(lambda s: received.append(f"cmd:{s}"))
        overlay.set_buddy_reaction_callback(
            lambda s: received.append(f"react:{s}"),
        )
        overlay.set_chat_persist_callback(
            persist=lambda s, t, u: received.append(f"persist:{s}"),
            clear=lambda: received.append("clear"),
        )

        # schedule_callback marshal — emit from this thread and confirm
        # the queued slot fires before teardown.
        fired: list[bool] = []
        overlay.schedule_callback(lambda: fired.append(True), delay_ms=0)
        overlay.schedule_callback(lambda: fired.append(True), delay_ms=20)

        _pump(qapp, ms=100)
        assert fired == [True, True], "schedule_callback didn't run on UI thread"

        # User-submit path should hit the registered callback.
        overlay._on_user_submit("hello from input")
        overlay._on_user_submit("/help")
        _pump(qapp, ms=20)
        assert "input:hello from input" in received
        assert "cmd:/help" in received
        assert "persist:you" in received
    finally:
        overlay.teardown()
        _pump(qapp, ms=20)


def test_qt_overlay_buffers_calls_before_setup(qapp: QApplication) -> None:
    """Brain may call adapter methods before setup() runs. The overlay
    stashes state into _pending_* buffers and drains on mount."""
    overlay = QtOverlay(config={})
    overlay.load_chat_history([(1_700_000_000.0, "you", "earlier", None)])
    overlay.set_voice_name("Early")
    overlay.update_status("preboot")

    overlay.setup()
    try:
        _pump(qapp, ms=30)
        assert overlay._chat is not None
        # History loaded into the chat widget.
        doc_text = overlay._chat._log.toPlainText()
        assert "earlier" in doc_text
        assert overlay._chat.windowTitle().startswith("Early")
    finally:
        overlay.teardown()
        _pump(qapp, ms=20)
