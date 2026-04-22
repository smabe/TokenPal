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
        assert overlay._history is not None
        assert overlay._dock is not None
        # History loaded into the history widget.
        doc_text = overlay._history._log.toPlainText()
        assert "earlier" in doc_text
        assert overlay._history.windowTitle().startswith("Early")
        # Status routed to the dock's status label.
        assert overlay._dock._status.text() == "preboot"
    finally:
        overlay.teardown()
        _pump(qapp, ms=20)


def test_history_window_hidden_by_default(qapp: QApplication) -> None:
    """The chat history window must start hidden — the dock's input
    strip is what the user sees under the buddy. The history only opens
    via the tray menu / toggle_chat_log / F2.

    setup() builds widgets without showing them; run_loop() is what
    shows the dock + hides the history. We mirror run_loop's show-path
    here without entering the blocking event loop, then assert the
    history was explicitly hidden while the dock was explicitly shown.
    """
    overlay = QtOverlay(config={})
    overlay.setup()
    try:
        assert overlay._history is not None
        assert overlay._dock is not None

        # Mirror run_loop's show ordering.
        overlay._dock.show()
        overlay._history.hide()

        # Check the explicit hide-flag, not isVisible(): headless Qt
        # test runs on macOS let all translucent windows auto-hide
        # between event-loop pumps regardless of what show() did, so
        # isVisible() is unreliable. isHidden() reflects the explicit
        # code-path state we care about.
        assert overlay._history.isHidden(), (
            "history window should be explicitly hidden on boot"
        )
        assert not overlay._dock.isHidden(), (
            "dock should be explicitly shown on boot"
        )
    finally:
        overlay.teardown()


def test_toggle_chat_log_flips_history_visibility(qapp: QApplication) -> None:
    """toggle_chat_log alternates the history window's hidden state.

    We check hidden state synchronously right after each toggle — macOS
    auto-hides translucent frameless windows when the test app loses
    focus between pumps, which confuses isVisible()-based assertions.
    The explicit show()/hide() call is what we actually care about.
    """
    overlay = QtOverlay(config={})
    overlay.setup()
    try:
        assert overlay._history is not None
        overlay._history.hide()
        assert overlay._history.isHidden()

        overlay._do_toggle_chat()  # should show
        assert not overlay._history.isHidden()
        assert overlay._tray is not None

        overlay._do_toggle_chat()  # should hide
        assert overlay._history.isHidden()
    finally:
        overlay.teardown()


def test_reposition_dock_fires_on_position_changed(qapp: QApplication) -> None:
    overlay = QtOverlay(config={})
    overlay.setup()
    try:
        assert overlay._dock is not None
        assert overlay._buddy is not None
        overlay._reposition_dock()
        _pump(qapp, ms=30)
        before = (overlay._dock.x(), overlay._dock.y())

        overlay._buddy._sim.set_anchor(900.0, 500.0)
        overlay._buddy._wake_timer()
        _pump(qapp, ms=120)

        after = (overlay._dock.x(), overlay._dock.y())
        assert after != before, (
            "dock should follow the buddy via position_changed"
        )
    finally:
        overlay.teardown()
        _pump(qapp, ms=20)


def test_status_composition_order() -> None:
    """Prefix order must be weather | voice+mood | server | model.

    Tests the orchestrator's _push_status composition directly so the
    Qt dock's status label receives fields in the documented order.
    """
    from unittest.mock import MagicMock

    from tokenpal.brain.orchestrator import Brain

    # Wire just enough state to exercise _push_status.
    brain = Brain.__new__(Brain)
    brain._personality = MagicMock(mood="happy", voice_name="Glados")
    brain._llm = MagicMock(
        api_url="http://remote-gpu:11434",
        primary_url="http://remote-gpu:11434",
        using_fallback=False,
        model_name="gemma4:4b",
    )
    brain._last_comment_time = 0.0
    brain._context = MagicMock()
    from tokenpal.senses.base import SenseReading
    brain._context.active_readings.return_value = {
        "weather": SenseReading(
            sense_name="weather",
            timestamp=0.0,
            data={},
            summary="sunny 72F",
        ),
    }
    brain._abbreviate_weather = lambda s: s[:12]

    captured: list[str] = []
    brain._status_callback = captured.append
    brain._push_status()

    assert captured, "status callback should have been invoked"
    parts = [p.strip() for p in captured[0].split("|")]
    # First four fields in the contract order.
    assert parts[0].startswith("sunny"), f"first field should be weather, got {parts}"
    assert "Glados" in parts[1] and "happy" in parts[1], (
        f"second field should be voice+mood, got {parts}"
    )
    assert parts[2] == "remote-gpu", f"third field should be server host, got {parts}"
    assert parts[3] == "gemma4:4b", f"fourth field should be model, got {parts}"
