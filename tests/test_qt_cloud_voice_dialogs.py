"""Contract tests for the Qt cloud + voice sub-modals.

The Options modal's launcher buttons for Cloud LLM and Voice route
through app.py → overlay.open_cloud_modal / open_voice_modal. Before
this phase those inherited the AbstractOverlay False default, so
clicking them closed the options screen and nothing opened. These
tests cover the Qt dialog contract and the QtOverlay wiring.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from tokenpal.tools.voice_profile import ProfileSummary  # noqa: E402
from tokenpal.ui.cloud_modal import CloudModalResult, CloudModalState  # noqa: E402
from tokenpal.ui.qt.cloud_dialog import CloudDialog  # noqa: E402
from tokenpal.ui.qt.overlay import QtOverlay  # noqa: E402
from tokenpal.ui.qt.voice_dialog import VoiceDialog  # noqa: E402
from tokenpal.ui.voice_modal import VoiceModalResult, VoiceModalState  # noqa: E402


@pytest.fixture
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


# --- Cloud dialog -------------------------------------------------------


def _cloud_state() -> CloudModalState:
    return CloudModalState(
        enabled=False,
        research_synth=True,
        research_plan=False,
        research_deep=False,
        research_search=False,
        model="claude-haiku-4-5",
        key_fingerprint=None,
    )


def test_cloud_save_returns_result(qapp: QApplication) -> None:
    captured: list[CloudModalResult | None] = []
    dlg = CloudDialog(_cloud_state(), captured.append)
    dlg._enabled.setChecked(True)
    dlg._plan.setChecked(True)
    dlg._api_key.setText("sk-test-abc")
    dlg._on_save()
    assert captured[0] is not None
    r = captured[0]
    assert r.enabled is True
    assert r.research_plan is True
    assert r.new_api_key == "sk-test-abc"


def test_cloud_blank_key_preserves_stored(qapp: QApplication) -> None:
    captured: list[CloudModalResult | None] = []
    dlg = CloudDialog(_cloud_state(), captured.append)
    dlg._on_save()
    assert captured[0] is not None
    assert captured[0].new_api_key is None


def test_cloud_cancel_fires_none(qapp: QApplication) -> None:
    captured: list[CloudModalResult | None] = []
    dlg = CloudDialog(_cloud_state(), captured.append)
    dlg._on_cancel()
    assert captured == [None]


def test_cloud_deep_without_sonnet_snaps_off(qapp: QApplication) -> None:
    """Deep mode requires Sonnet+; Haiku + deep would misbehave at
    runtime, so the dialog guardrails it off at save-time."""
    captured: list[CloudModalResult | None] = []
    dlg = CloudDialog(_cloud_state(), captured.append)
    dlg._deep.setChecked(True)
    dlg._model.setCurrentText("claude-haiku-4-5")
    dlg._on_save()
    assert captured[0] is not None
    assert captured[0].research_deep is False


def test_cloud_refine_cap_clamps_out_of_range(qapp: QApplication) -> None:
    captured: list[CloudModalResult | None] = []
    dlg = CloudDialog(_cloud_state(), captured.append)
    dlg._refine_cap.setText("99")
    dlg._on_save()
    assert captured[0] is not None
    assert 0 <= captured[0].refine_max_supplemental <= 10


# --- Voice dialog -------------------------------------------------------


def _voice_state(
    active: ProfileSummary | None = None, cloud_ready: bool = False,
) -> VoiceModalState:
    saved = [
        ProfileSummary(
            slug="rick", character="Rick",
            line_count=42, source="fandom", finetuned_model="",
        ),
        ProfileSummary(
            slug="morty", character="Morty",
            line_count=38, source="fandom", finetuned_model="",
        ),
    ]
    return VoiceModalState(
        active_voice=active, saved=saved, cloud_ready=cloud_ready,
    )


def test_voice_switch_returns_selected_character(qapp: QApplication) -> None:
    captured: list[VoiceModalResult | None] = []
    dlg = VoiceDialog(_voice_state(), captured.append)
    dlg._voice_list.setCurrentRow(1)  # Morty
    dlg._action_switch()
    assert captured == [
        VoiceModalResult(action="switch", payload={"name": "Morty"}),
    ]


def test_voice_switch_noop_when_nothing_selected(qapp: QApplication) -> None:
    captured: list[VoiceModalResult | None] = []
    dlg = VoiceDialog(_voice_state(), captured.append)
    dlg._action_switch()
    assert captured == []


def test_voice_off_action(qapp: QApplication) -> None:
    captured: list[VoiceModalResult | None] = []
    active = ProfileSummary(
        slug="rick", character="Rick",
        line_count=42, source="fandom", finetuned_model="",
    )
    dlg = VoiceDialog(_voice_state(active=active), captured.append)
    dlg._action_off()
    assert captured == [VoiceModalResult(action="off")]


def test_voice_train_requires_both_fields(qapp: QApplication) -> None:
    captured: list[VoiceModalResult | None] = []
    dlg = VoiceDialog(_voice_state(), captured.append)
    dlg._wiki_input.setText("https://example.com")
    dlg._action_train()  # empty character → should not fire
    assert captured == []
    dlg._character_input.setText("TestBuddy")
    dlg._action_train()
    assert captured == [
        VoiceModalResult(
            action="train",
            payload={"wiki": "https://example.com", "character": "TestBuddy"},
        ),
    ]


def test_voice_cancel_fires_none(qapp: QApplication) -> None:
    captured: list[VoiceModalResult | None] = []
    dlg = VoiceDialog(_voice_state(), captured.append)
    dlg._on_cancel()
    assert captured == [None]


# --- QtOverlay wiring ---------------------------------------------------


def test_qt_overlay_open_cloud_modal_returns_true(qapp: QApplication) -> None:
    overlay = QtOverlay(config={})
    overlay.setup()
    try:
        assert overlay.open_cloud_modal(_cloud_state(), lambda _r: None) is True
    finally:
        overlay.teardown()


def test_qt_overlay_open_voice_modal_returns_true(qapp: QApplication) -> None:
    overlay = QtOverlay(config={})
    overlay.setup()
    try:
        assert overlay.open_voice_modal(_voice_state(), lambda _r: None) is True
    finally:
        overlay.teardown()
