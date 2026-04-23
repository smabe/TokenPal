"""Qt port of the /cloud settings modal.

Mirrors tokenpal/ui/cloud_modal.py — same CloudModalState in, same
CloudModalResult out — so the app-layer dispatch in app.py stays
unchanged.

Sections:
- Anthropic: enabled, API key (masked, optional replace), model dropdown,
  research plan / search / deep toggles.
- Tavily: enabled, API key, search depth (basic / advanced).
- Brave: API key only.
- /refine supplemental cap.

Save / Cancel. Empty key inputs mean "keep whatever's on disk"; a value
typed into a key field is a replacement.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from tokenpal.llm.cloud_backend import ALLOWED_MODELS, DEEP_MODE_MODELS
from tokenpal.ui.cloud_modal import CloudModalResult, CloudModalState
from tokenpal.ui.qt.modals import _OneShotCallback


def _header(text: str) -> QLabel:
    label = QLabel(f"<b>{text}</b>")
    label.setStyleSheet("color: #4ade80")
    return label


def _help(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("color: #888")
    label.setWordWrap(True)
    return label


class CloudDialog(QDialog, _OneShotCallback):
    def __init__(
        self,
        state: CloudModalState,
        on_result: Callable[[CloudModalResult | None], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Cloud LLM")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.resize(520, 660)
        self._state = state
        self._on_result = on_result
        self._fired = False
        self._callback_name = "CloudDialog"

        layout = QVBoxLayout(self)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_layout = QVBoxLayout(body)

        self._build_anthropic(body_layout)
        self._build_tavily(body_layout)
        self._build_brave(body_layout)
        self._build_refine(body_layout)

        body_layout.addStretch(1)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self._on_cancel)
        layout.addWidget(buttons)

    # --- Sections --------------------------------------------------------

    def _build_anthropic(self, parent: QVBoxLayout) -> None:
        parent.addWidget(_header("Anthropic"))
        fp = self._state.key_fingerprint or "no key stored"
        parent.addWidget(_help(f"Current key: {fp}"))

        self._enabled = QCheckBox("Enable cloud synth for /research")
        self._enabled.setChecked(self._state.enabled)
        parent.addWidget(self._enabled)

        key_row = QHBoxLayout()
        key_row.addWidget(QLabel("API key:"))
        self._api_key = QLineEdit()
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key.setPlaceholderText(
            "leave blank to keep the stored key",
        )
        key_row.addWidget(self._api_key, 1)
        parent.addLayout(key_row)

        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Model:"))
        self._model = QComboBox()
        for m in ALLOWED_MODELS:
            self._model.addItem(m)
        if self._state.model in ALLOWED_MODELS:
            self._model.setCurrentText(self._state.model)
        model_row.addWidget(self._model, 1)
        parent.addLayout(model_row)

        self._plan = QCheckBox("Cloud-assisted research plan")
        self._plan.setChecked(self._state.research_plan)
        parent.addWidget(self._plan)

        self._search = QCheckBox(
            "Web search in synth (Sonnet+ only; skips local fetch loop)",
        )
        self._search.setChecked(self._state.research_search)
        parent.addWidget(self._search)

        self._deep = QCheckBox(
            "Deep mode: full-page web_fetch (Sonnet+; costly)",
        )
        self._deep.setChecked(self._state.research_deep)
        parent.addWidget(self._deep)

    def _build_tavily(self, parent: QVBoxLayout) -> None:
        parent.addWidget(_header("Tavily (LLM-optimized search)"))
        fp = self._state.tavily_key_fingerprint or "no key stored"
        parent.addWidget(_help(f"Current key: {fp}"))

        self._tavily_enabled = QCheckBox(
            "Route /research through Tavily when available",
        )
        self._tavily_enabled.setChecked(self._state.tavily_enabled)
        parent.addWidget(self._tavily_enabled)

        key_row = QHBoxLayout()
        key_row.addWidget(QLabel("API key:"))
        self._tavily_key = QLineEdit()
        self._tavily_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._tavily_key.setPlaceholderText(
            "leave blank to keep the stored key",
        )
        key_row.addWidget(self._tavily_key, 1)
        parent.addLayout(key_row)

        depth_row = QHBoxLayout()
        depth_row.addWidget(QLabel("Search depth:"))
        self._depth_basic = QRadioButton("basic (1 credit)")
        self._depth_advanced = QRadioButton("advanced (2 credits)")
        (self._depth_advanced
         if self._state.tavily_search_depth == "advanced"
         else self._depth_basic).setChecked(True)
        depth_group = QButtonGroup(self)
        depth_group.addButton(self._depth_basic)
        depth_group.addButton(self._depth_advanced)
        self._depth_group = depth_group  # retain reference
        depth_row.addWidget(self._depth_basic)
        depth_row.addWidget(self._depth_advanced)
        depth_row.addStretch(1)
        parent.addLayout(depth_row)

    def _build_brave(self, parent: QVBoxLayout) -> None:
        parent.addWidget(_header("Brave (web search)"))
        fp = self._state.brave_key_fingerprint or "no key stored"
        parent.addWidget(_help(
            f"Current key: {fp}. Presence of a key = active (no enabled flag).",
        ))
        key_row = QHBoxLayout()
        key_row.addWidget(QLabel("API key:"))
        self._brave_key = QLineEdit()
        self._brave_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._brave_key.setPlaceholderText(
            "leave blank to keep the stored key",
        )
        key_row.addWidget(self._brave_key, 1)
        parent.addLayout(key_row)

    def _build_refine(self, parent: QVBoxLayout) -> None:
        parent.addWidget(_header("/refine supplemental cap"))
        parent.addWidget(_help(
            "0 disables supplemental search during /refine — it just "
            "re-synthesizes the cached sources. Typical cap: 2.",
        ))
        row = QHBoxLayout()
        row.addWidget(QLabel("Max supplemental searches:"))
        self._refine_cap = QLineEdit(str(self._state.refine_max_supplemental))
        self._refine_cap.setValidator(QIntValidator(0, 10))
        self._refine_cap.setMaxLength(2)
        row.addWidget(self._refine_cap, 1)
        parent.addLayout(row)

    # --- Save / Cancel ---------------------------------------------------

    def _on_save(self) -> None:
        self._deliver(self._on_result, self._collect())
        self.accept()

    def _on_cancel(self) -> None:
        self._deliver(self._on_result, None)
        self.reject()

    def _collect(self) -> CloudModalResult:
        try:
            refine_cap = int(self._refine_cap.text() or "0")
        except ValueError:
            refine_cap = self._state.refine_max_supplemental
        refine_cap = max(0, min(refine_cap, 10))

        deep = self._deep.isChecked()
        model = self._model.currentText()
        # Mirror the Textual modal's guardrail: deep mode requires a
        # Sonnet+ model. If the user leaves Haiku + deep on, snap deep
        # off rather than silently misbehaving at runtime.
        if deep and model not in DEEP_MODE_MODELS:
            deep = False

        return CloudModalResult(
            enabled=self._enabled.isChecked(),
            research_synth=self._state.research_synth,
            research_plan=self._plan.isChecked(),
            research_deep=deep,
            research_search=self._search.isChecked(),
            model=model,
            new_api_key=self._api_key.text() or None,
            tavily_enabled=self._tavily_enabled.isChecked(),
            tavily_search_depth=(
                "advanced" if self._depth_advanced.isChecked() else "basic"
            ),
            tavily_new_api_key=self._tavily_key.text() or None,
            brave_new_api_key=self._brave_key.text() or None,
            refine_max_supplemental=refine_cap,
        )
