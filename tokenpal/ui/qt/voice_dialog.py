"""Qt port of the /voice management modal.

Covers the common-case actions so the tray's Options → Voice path
actually does something. Mirrors VoiceModalState / VoiceModalResult
from tokenpal/ui/voice_modal.py so the app.py dispatcher stays
unchanged.

Actions wired:
- switch: pick a saved voice from the list → dismisses with
  action="switch", payload={"name": <character>}.
- off: revert to the default TokenPal voice.
- train: wiki URL + character inputs → action="train".
- regenerate: regenerate the active voice's art (confirm-gated
  upstream in app.py).
- ascii: refresh just the ASCII frames for the active voice.
- cloud_classifier: toggle the Haiku-backed classifier flag.

Other actions (finetune, finetune_setup, import) are parked — the
slash commands still work for those.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from tokenpal.ui.qt.modals import _OneShotCallback
from tokenpal.ui.voice_modal import VoiceModalResult, VoiceModalState


def _header(text: str) -> QLabel:
    label = QLabel(f"<b>{text}</b>")
    label.setStyleSheet("color: #4ade80")
    return label


def _help(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("color: #888")
    label.setWordWrap(True)
    return label


class VoiceDialog(QDialog, _OneShotCallback):
    def __init__(
        self,
        state: VoiceModalState,
        on_result: Callable[[VoiceModalResult | None], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Voice")
        self.setModal(True)
        self.resize(520, 620)
        self._state = state
        self._on_result = on_result
        self._fired = False
        self._callback_name = "VoiceDialog"

        layout = QVBoxLayout(self)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_layout = QVBoxLayout(body)

        self._build_status(body_layout)
        self._build_saved_voices(body_layout)
        self._build_train(body_layout)
        self._build_maintenance(body_layout)
        self._build_cloud_classifier(body_layout)

        body_layout.addStretch(1)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

        # Only Close here — every action button dismisses the dialog
        # itself with the right result, so a bare "Save" wouldn't know
        # what to emit.
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self._on_cancel)
        layout.addWidget(buttons)

    # --- Sections --------------------------------------------------------

    def _build_status(self, parent: QVBoxLayout) -> None:
        parent.addWidget(_header("Active voice"))
        if self._state.active_voice is None:
            parent.addWidget(_help("Default TokenPal voice"))
        else:
            av = self._state.active_voice
            parent.addWidget(_help(
                f"{av.character} — {av.line_count} lines — "
                f"source: {av.source or 'unknown'}",
            ))
            off_btn = QPushButton("Use default voice")
            off_btn.clicked.connect(self._action_off)
            parent.addWidget(off_btn)

    def _build_saved_voices(self, parent: QVBoxLayout) -> None:
        parent.addWidget(_header("Saved voices"))
        if not self._state.saved:
            parent.addWidget(_help("No saved voices yet. Train one below."))
            return
        self._voice_list = QListWidget()
        for summary in self._state.saved:
            item = QListWidgetItem(
                f"{summary.character} ({summary.line_count} lines)",
            )
            item.setData(0x0100, summary.character)  # Qt.ItemDataRole.UserRole = 0x0100
            self._voice_list.addItem(item)
        parent.addWidget(self._voice_list)
        switch_btn = QPushButton("Switch to selected")
        switch_btn.clicked.connect(self._action_switch)
        parent.addWidget(switch_btn)

    def _build_train(self, parent: QVBoxLayout) -> None:
        parent.addWidget(_header("Train a new voice"))
        parent.addWidget(_help(
            "Wiki URL + character name. Takes ~60s of LLM time.",
        ))
        self._wiki_input = QLineEdit()
        self._wiki_input.setPlaceholderText("https://fandom.com/wiki/...")
        parent.addWidget(self._wiki_input)
        self._character_input = QLineEdit()
        self._character_input.setPlaceholderText("Character name")
        parent.addWidget(self._character_input)
        train_btn = QPushButton("Train")
        train_btn.clicked.connect(self._action_train)
        parent.addWidget(train_btn)

    def _build_maintenance(self, parent: QVBoxLayout) -> None:
        parent.addWidget(_header("Maintenance"))
        active_name = (
            self._state.active_voice.character
            if self._state.active_voice is not None else ""
        )
        if not active_name:
            parent.addWidget(_help(
                "Switch to a custom voice to enable these.",
            ))
            return
        row = QHBoxLayout()
        regen_btn = QPushButton("Regenerate (~60s)")
        regen_btn.clicked.connect(
            lambda: self._action_with_name("regenerate", active_name),
        )
        ascii_btn = QPushButton("ASCII only")
        ascii_btn.clicked.connect(
            lambda: self._action_with_name("ascii", active_name),
        )
        row.addWidget(regen_btn)
        row.addWidget(ascii_btn)
        parent.addLayout(row)

    def _build_cloud_classifier(self, parent: QVBoxLayout) -> None:
        parent.addWidget(_header("ASCII classifier"))
        if not self._state.cloud_ready:
            parent.addWidget(_help(
                "Haiku-backed ASCII classifier is unavailable — "
                "enable Cloud LLM + set an Anthropic key first.",
            ))
            return
        self._classifier_cb = QCheckBox(
            "Use Haiku to pick ASCII skeleton + palette at voice-load",
        )
        self._classifier_cb.setChecked(self._state.voice_classifier_on)
        parent.addWidget(self._classifier_cb)
        apply_btn = QPushButton("Apply classifier change")
        apply_btn.clicked.connect(self._action_cloud_classifier)
        parent.addWidget(apply_btn)

    # --- Action dispatch -------------------------------------------------

    def _action_off(self) -> None:
        self._deliver(self._on_result, VoiceModalResult(action="off"))
        self.accept()

    def _action_switch(self) -> None:
        item = self._voice_list.currentItem()
        if item is None:
            return
        name = item.data(0x0100)
        if not isinstance(name, str) or not name:
            return
        self._deliver(
            self._on_result,
            VoiceModalResult(action="switch", payload={"name": name}),
        )
        self.accept()

    def _action_train(self) -> None:
        wiki = self._wiki_input.text().strip()
        character = self._character_input.text().strip()
        if not wiki or not character:
            return
        self._deliver(
            self._on_result,
            VoiceModalResult(
                action="train",
                payload={"wiki": wiki, "character": character},
            ),
        )
        self.accept()

    def _action_with_name(self, action: str, name: str) -> None:
        self._deliver(
            self._on_result,
            VoiceModalResult(
                action=action,  # type: ignore[arg-type]
                payload={"name": name},
            ),
        )
        self.accept()

    def _action_cloud_classifier(self) -> None:
        enabled = "true" if self._classifier_cb.isChecked() else "false"
        self._deliver(
            self._on_result,
            VoiceModalResult(
                action="cloud_classifier", payload={"enabled": enabled},
            ),
        )
        self.accept()

    def _on_cancel(self) -> None:
        self._deliver(self._on_result, None)
        self.reject()
