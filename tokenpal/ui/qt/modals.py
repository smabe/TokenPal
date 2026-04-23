"""Qt versions of the generic overlay modals.

Phase 4 scope: selection + confirm. The richer modals (cloud, options,
voice) route through their existing slash-command fallbacks — the
adapter's inherited-False defaults trigger the text UI, and parity for
those lands in a later pass.

Both dialogs use Qt's standard ``QDialog`` lifecycle: construct, wire
up a result callback, ``exec()`` on the main thread. The caller never
blocks — it hands in ``on_result`` / ``on_save`` and returns.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from tokenpal.ui.selection_modal import SelectionGroup

log = logging.getLogger(__name__)


class _OneShotCallback:
    """Shared guard for dialogs that must deliver their result exactly
    once. ``accepted`` / ``rejected`` cover every user-visible exit
    (Yes / No, Save / Cancel, Esc, X) — we deliberately don't listen to
    ``finished`` because it fires again on every ``accept()`` /
    ``reject()`` and would double-invoke the callback."""

    _fired: bool
    _callback_name: str

    def _deliver(self, callback: Callable[..., None], value: Any) -> None:
        if getattr(self, "_fired", False):
            return
        self._fired = True
        try:
            callback(value)
        except Exception:
            log.exception("%s callback raised", self._callback_name)


class ConfirmDialog(QDialog, _OneShotCallback):
    """Yes / No prompt. ``on_result(bool)`` fires once when closed."""

    def __init__(
        self,
        title: str,
        body: str,
        on_result: Callable[[bool], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self._on_result = on_result
        self._fired = False
        self._callback_name = "ConfirmDialog"

        layout = QVBoxLayout(self)
        label = QLabel(body)
        label.setWordWrap(True)
        layout.addWidget(label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Yes
            | QDialogButtonBox.StandardButton.No,
        )
        buttons.accepted.connect(self._on_yes)
        buttons.rejected.connect(self._on_no)
        layout.addWidget(buttons)

    def _on_yes(self) -> None:
        self._deliver(self._on_result, True)
        self.accept()

    def _on_no(self) -> None:
        self._deliver(self._on_result, False)
        self.reject()


class SelectionDialog(QDialog, _OneShotCallback):
    """Multi-group checkbox picker. Each ``SelectionGroup`` becomes a
    titled section of checkboxes. ``locked`` items render disabled but
    are always reported as selected in the result dict so the caller
    doesn't need a special case."""

    def __init__(
        self,
        title: str,
        groups: Sequence[SelectionGroup],
        on_save: Callable[[dict[str, list[str]] | None], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.resize(460, 520)
        self._on_save = on_save
        self._fired = False
        self._callback_name = "SelectionDialog"
        # group_title -> list[(value, checkbox, locked)]
        self._entries: dict[str, list[tuple[str, QCheckBox, bool]]] = {}

        layout = QVBoxLayout(self)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)

        for group in groups:
            header = QLabel(f"<b>{group.title}</b>")
            inner_layout.addWidget(header)
            if group.help_text:
                help_label = QLabel(group.help_text)
                help_label.setStyleSheet("color: #888")
                help_label.setWordWrap(True)
                inner_layout.addWidget(help_label)

            form = QFormLayout()
            rows: list[tuple[str, QCheckBox, bool]] = []
            for item in group.items:
                cb = QCheckBox(item.label)
                cb.setChecked(item.initial or item.locked)
                if item.locked:
                    cb.setEnabled(False)
                form.addRow(cb)
                rows.append((item.value, cb, item.locked))
            inner_layout.addLayout(form)
            self._entries[group.title] = rows

        inner_layout.addStretch(1)
        scroll.setWidget(inner)
        layout.addWidget(scroll, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self._on_cancel)
        layout.addWidget(buttons)

    def _collect(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for title, rows in self._entries.items():
            out[title] = [value for value, cb, _locked in rows if cb.isChecked()]
        return out

    def _on_accept(self) -> None:
        self._deliver(self._on_save, self._collect())
        self.accept()

    def _on_cancel(self) -> None:
        self._deliver(self._on_save, None)
        self.reject()


def _focus_dialog(dialog: QDialog) -> None:
    """Bring a modal to front. Always-on-top buddy windows otherwise
    obscure the dialog on macOS."""
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    dialog.setWindowState(
        dialog.windowState() & ~Qt.WindowState.WindowMinimized
        | Qt.WindowState.WindowActive,
    )
