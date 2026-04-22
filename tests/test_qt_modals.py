"""Qt modal contract — selection + confirm.

Exercises both dialogs at the QDialog API level and via QtOverlay's
open_*_modal entry points. Callbacks must fire exactly once, with
correct values, and must not re-fire if the dialog is dismissed multiple
times.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tokenpal.ui.qt.modals import ConfirmDialog, SelectionDialog  # noqa: E402
from tokenpal.ui.qt.overlay import QtOverlay  # noqa: E402
from tokenpal.ui.selection_modal import SelectionGroup, SelectionItem  # noqa: E402


@pytest.fixture
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


def _pump(qapp: QApplication, ms: int = 30) -> None:
    QTimer.singleShot(ms, qapp.quit)
    qapp.exec()


# --- ConfirmDialog ------------------------------------------------------


def test_confirm_yes_fires_true(qapp: QApplication) -> None:
    result: list[bool] = []
    dlg = ConfirmDialog("Ship it?", "for real?", result.append)
    dlg._on_yes()
    assert result == [True]


def test_confirm_no_fires_false(qapp: QApplication) -> None:
    result: list[bool] = []
    dlg = ConfirmDialog("Ship it?", "for real?", result.append)
    dlg._on_no()
    assert result == [False]


def test_confirm_callback_fires_exactly_once(qapp: QApplication) -> None:
    """A double-click on Yes shouldn't fire the callback twice."""
    result: list[bool] = []
    dlg = ConfirmDialog("Ship it?", "for real?", result.append)
    dlg._on_yes()
    dlg._on_yes()
    dlg._on_no()  # stray late rejection after accept
    assert result == [True]


# --- SelectionDialog ----------------------------------------------------


def _groups() -> list[SelectionGroup]:
    return [
        SelectionGroup(
            title="senses",
            items=(
                SelectionItem(value="time", label="time_awareness", initial=True),
                SelectionItem(value="idle", label="idle", initial=False),
            ),
        ),
        SelectionGroup(
            title="locked",
            items=(
                SelectionItem(
                    value="fixed", label="required", initial=True, locked=True,
                ),
            ),
        ),
    ]


def test_selection_save_returns_checked_values(qapp: QApplication) -> None:
    captured: list[dict[str, list[str]] | None] = []
    dlg = SelectionDialog("senses", _groups(), captured.append)
    # Toggle the second item on in the "senses" group.
    dlg._entries["senses"][1][1].setChecked(True)
    dlg._on_accept()
    assert captured == [{"senses": ["time", "idle"], "locked": ["fixed"]}]


def test_selection_cancel_returns_none(qapp: QApplication) -> None:
    captured: list[dict[str, list[str]] | None] = []
    dlg = SelectionDialog("senses", _groups(), captured.append)
    dlg._on_cancel()
    assert captured == [None]


def test_locked_item_cannot_be_toggled(qapp: QApplication) -> None:
    dlg = SelectionDialog("senses", _groups(), lambda _r: None)
    locked_cb = dlg._entries["locked"][0][1]
    assert locked_cb.isChecked()
    assert not locked_cb.isEnabled()


# --- QtOverlay wiring ---------------------------------------------------


def test_qt_overlay_reports_modals_are_supported(qapp: QApplication) -> None:
    overlay = QtOverlay(config={})
    overlay.setup()
    try:
        # Don't actually exec() the dialog — just confirm the overlay
        # reports support. The dialog construction is tested directly
        # above.
        handled = overlay.open_confirm_modal("Ship it?", "sure?", lambda _: None)
        assert handled is True
        handled = overlay.open_selection_modal(
            "senses", _groups(), lambda _r: None,
        )
        assert handled is True
        _pump(qapp, ms=50)
    finally:
        overlay.teardown()
        _pump(qapp, ms=20)
