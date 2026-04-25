"""Qt options dialog — contract tests.

Exercises every button path against the OptionsModalState / Result
contract the Textual version already ships. QtOverlay.open_options_modal
is tested via the _OneShotCallback guard so tests can invoke button
handlers directly without exec()-ing the dialog.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from tokenpal.ui.options_modal import (  # noqa: E402
    OptionsModalResult,
    OptionsModalState,
    ServerEntry,
)
from tokenpal.ui.qt.options_dialog import OptionsDialog  # noqa: E402
from tokenpal.ui.qt.overlay import QtOverlay  # noqa: E402


@pytest.fixture
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


def _state() -> OptionsModalState:
    return OptionsModalState(
        max_persisted=200,
        persist_enabled=True,
        current_api_url="http://localhost:11434/v1",
        known_servers=(
            ServerEntry(
                url="http://localhost:11434/v1", label="local", model="gemma4",
            ),
            ServerEntry(
                url="http://192.168.1.50:11434/v1",
                label="remote", model="qwen3-14b",
            ),
        ),
        current_model="gemma4",
        available_models=("gemma4", "llama3"),
        weather_label="90210",
        current_wifi_label="home",
    )


def test_save_returns_result_with_current_max_persisted(
    qapp: QApplication,
) -> None:
    captured: list[OptionsModalResult | None] = []
    dlg = OptionsDialog(_state(), captured.append)
    dlg._max_persisted_input.setText("150")
    dlg._on_save()
    assert len(captured) == 1
    result = captured[0]
    assert result is not None
    assert result.max_persisted == 150
    assert result.clear_history is False
    assert result.navigate_to is None
    assert result.switch_server_to is None
    assert result.switch_model_to is None
    # No font interaction → None (baselined at dialog construction).
    assert result.set_chat_font is None
    assert result.set_bubble_font is None


def test_cancel_fires_none(qapp: QApplication) -> None:
    captured: list[OptionsModalResult | None] = []
    dlg = OptionsDialog(_state(), captured.append)
    dlg._on_cancel()
    assert captured == [None]


def test_clear_history_button_sets_flag(qapp: QApplication) -> None:
    captured: list[OptionsModalResult | None] = []
    dlg = OptionsDialog(_state(), captured.append)
    dlg._on_clear_history()
    assert captured[0] is not None
    assert captured[0].clear_history is True


def test_audio_toggles_round_trip(qapp: QApplication) -> None:
    """Both checkboxes mirror the state on construction and ride in the
    result after Save."""
    state = OptionsModalState(
        max_persisted=200,
        persist_enabled=True,
        voice_conversation_enabled=True,
        speak_ambient_enabled=False,
    )
    captured: list[OptionsModalResult | None] = []
    dlg = OptionsDialog(state, captured.append)
    assert dlg._voice_conversation_cb.isChecked() is True
    assert dlg._speak_ambient_cb.isChecked() is False

    dlg._speak_ambient_cb.setChecked(True)
    dlg._on_save()
    result = captured[0]
    assert result is not None
    assert result.voice_conversation_enabled is True
    assert result.speak_ambient_enabled is True


def test_zip_apply_fires_partial_and_keeps_dialog_open(
    qapp: QApplication,
) -> None:
    captured: list[OptionsModalResult | None] = []
    dlg = OptionsDialog(_state(), captured.append)
    dlg._zip_input.setText("94103")
    dlg._on_apply_zip()
    assert captured[0] is not None
    assert captured[0].set_zip == "94103"
    # One-shot guard must NOT be armed — Save must still be able to fire.
    assert dlg._fired is False
    assert "94103" in dlg._zip_status.text()
    # Subsequent Save still delivers a final result.
    dlg._on_save()
    assert len(captured) == 2
    assert captured[1] is not None
    assert captured[1].set_zip is None


def test_wifi_apply_fires_partial_and_keeps_dialog_open(
    qapp: QApplication,
) -> None:
    captured: list[OptionsModalResult | None] = []
    dlg = OptionsDialog(_state(), captured.append)
    dlg._wifi_input.setText("office")
    dlg._on_apply_wifi()
    assert captured[0] is not None
    assert captured[0].set_wifi_label == "office"
    assert dlg._fired is False
    assert "office" in dlg._wifi_status.text()
    dlg._on_save()
    assert len(captured) == 2
    assert captured[1] is not None
    assert captured[1].set_wifi_label is None


def test_launcher_button_invokes_on_open_subdialog(qapp: QApplication) -> None:
    for target in ("cloud", "senses", "tools", "voice"):
        launched: list[str] = []
        result_captured: list[OptionsModalResult | None] = []
        dlg = OptionsDialog(
            _state(),
            result_captured.append,
            on_open_subdialog=launched.append,
        )
        dlg._on_launch(target)
        assert launched == [target]
        # Launcher must not fire on_result — Options stays open.
        assert result_captured == []


def test_selecting_other_server_tracks_pending_pick(qapp: QApplication) -> None:
    captured: list[OptionsModalResult | None] = []
    dlg = OptionsDialog(_state(), captured.append)
    # Pick the second server from the state's known_servers.
    remote_url = dlg._state.known_servers[1].url
    dlg._select_pending_server(remote_url)
    dlg._on_save()
    assert captured[0] is not None
    assert captured[0].switch_server_to == remote_url


def test_selecting_current_server_clears_pending(qapp: QApplication) -> None:
    """Clicking the already-active server row shouldn't queue a switch."""
    captured: list[OptionsModalResult | None] = []
    dlg = OptionsDialog(_state(), captured.append)
    # First pick a different server, then pick the current one back.
    dlg._select_pending_server(dlg._state.known_servers[1].url)
    dlg._select_pending_server(dlg._state.current_api_url)
    dlg._on_save()
    assert captured[0] is not None
    assert captured[0].switch_server_to is None


def test_custom_server_apply_sets_pending(qapp: QApplication) -> None:
    captured: list[OptionsModalResult | None] = []
    dlg = OptionsDialog(_state(), captured.append)
    dlg._custom_server_input.setText("http://10.0.0.5:11434")
    dlg._on_apply_custom_server()
    dlg._on_save()
    assert captured[0] is not None
    assert captured[0].switch_server_to == "http://10.0.0.5:11434"


def test_model_picker_reflects_active_server(qapp: QApplication) -> None:
    dlg = OptionsDialog(_state(), lambda _r: None)
    # Active server's models should populate the model list.
    assert dlg._model_list.count() == 2
    labels = [dlg._model_list.item(i).text() for i in range(2)]
    assert any("gemma4" in lbl for lbl in labels)
    assert any("llama3" in lbl for lbl in labels)


def test_model_picker_shows_placeholder_for_unknown_server(
    qapp: QApplication,
) -> None:
    dlg = OptionsDialog(_state(), lambda _r: None)
    dlg._select_pending_server("http://192.168.1.50:11434/v1")
    # Non-active server: no pre-populated models in state, so placeholder.
    assert dlg._model_list.count() == 1
    assert "probe" in dlg._model_list.item(0).text().lower()


def test_max_persisted_is_clamped_to_config_range(qapp: QApplication) -> None:
    from tokenpal.config.chatlog_writer import MAX_PERSISTED
    captured: list[OptionsModalResult | None] = []
    dlg = OptionsDialog(_state(), captured.append)
    dlg._max_persisted_input.setText(str(MAX_PERSISTED + 5000))
    dlg._on_save()
    assert captured[0] is not None
    assert captured[0].max_persisted <= MAX_PERSISTED


def test_empty_zip_apply_is_noop(qapp: QApplication) -> None:
    """Apply with an empty zip field must not dismiss the dialog —
    otherwise a stray click silently saves a blank location."""
    captured: list[OptionsModalResult | None] = []
    dlg = OptionsDialog(_state(), captured.append)
    dlg._zip_input.setText("")
    dlg._on_apply_zip()
    assert captured == []


def test_empty_wifi_apply_is_noop(qapp: QApplication) -> None:
    captured: list[OptionsModalResult | None] = []
    dlg = OptionsDialog(_state(), captured.append)
    dlg._wifi_input.setText("")
    dlg._on_apply_wifi()
    assert captured == []


def test_launcher_preserves_unsaved_max_persisted_edit(
    qapp: QApplication,
) -> None:
    """Non-modal Options keeps pending edits live across sub-dialog
    launches. The user's in-progress max_persisted edit must still be
    in the field when Save is eventually clicked."""
    result_captured: list[OptionsModalResult | None] = []
    dlg = OptionsDialog(
        _state(), result_captured.append, on_open_subdialog=lambda _t: None,
    )
    dlg._max_persisted_input.setText("9")
    dlg._on_launch("cloud")
    assert result_captured == []
    assert dlg._max_persisted_input.text() == "9"
    dlg._on_save()
    assert result_captured[0] is not None
    assert result_captured[0].max_persisted == 9


def test_qt_overlay_open_options_modal_returns_true(qapp: QApplication) -> None:
    overlay = QtOverlay(config={})
    overlay.setup()
    try:
        handled = overlay.open_options_modal(_state(), lambda _r: None)
        assert handled is True
    finally:
        overlay.teardown()


def test_commit_background_color_fires_preview_and_updates_swatch(
    qapp: QApplication,
) -> None:
    previews: list[str] = []
    dlg = OptionsDialog(
        _state(), lambda _r: None,
        on_background_color_preview=previews.append,
    )
    dlg._commit_background_color("#123456")
    assert previews == ["#123456"]
    assert dlg._current_background_color == "#123456"
    assert "#123456" in dlg._bg_color_swatch.styleSheet()


def test_commit_font_color_fires_preview_and_normalizes(
    qapp: QApplication,
) -> None:
    previews: list[str] = []
    dlg = OptionsDialog(
        _state(), lambda _r: None,
        on_font_color_preview=previews.append,
    )
    dlg._commit_font_color("#AbCdEf")
    assert previews == ["#abcdef"]
    assert dlg._current_font_color == "#abcdef"


def test_save_emits_color_changes_only_when_differing(
    qapp: QApplication,
) -> None:
    captured: list[OptionsModalResult | None] = []
    dlg = OptionsDialog(_state(), captured.append)
    # No interaction → both color fields remain None.
    dlg._on_save()
    assert captured[0] is not None
    assert captured[0].set_chat_history_background_color is None
    assert captured[0].set_chat_history_font_color is None


def test_save_emits_color_changes_after_commit(qapp: QApplication) -> None:
    captured: list[OptionsModalResult | None] = []
    dlg = OptionsDialog(_state(), captured.append)
    dlg._commit_background_color("#223344")
    dlg._commit_font_color("#eeffaa")
    dlg._on_save()
    assert captured[0] is not None
    assert captured[0].set_chat_history_background_color == "#223344"
    assert captured[0].set_chat_history_font_color == "#eeffaa"


def test_cancel_reverts_opacity_and_colors(qapp: QApplication) -> None:
    opacity_previews: list[float] = []
    bg_previews: list[str] = []
    fg_previews: list[str] = []
    state = _state()
    dlg = OptionsDialog(
        state, lambda _r: None,
        on_opacity_preview=opacity_previews.append,
        on_background_color_preview=bg_previews.append,
        on_font_color_preview=fg_previews.append,
    )
    dlg._commit_background_color("#ff0000")
    dlg._commit_font_color("#00ff00")
    dlg._on_opacity_changed(40)
    # The preview lists captured the user's mid-edit values.
    assert bg_previews[-1] == "#ff0000"
    assert fg_previews[-1] == "#00ff00"
    dlg._on_cancel()
    # Cancel must revert all three to their initial values.
    assert opacity_previews[-1] == pytest.approx(state.chat_history_opacity)
    assert bg_previews[-1] == state.chat_history_background_color
    assert fg_previews[-1] == state.chat_history_font_color
