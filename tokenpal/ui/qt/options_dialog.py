"""Qt port of the /options umbrella modal.

Mirrors the Textual version (tokenpal/ui/options_modal.py) — same
``OptionsModalState`` input, same ``OptionsModalResult`` output — so the
app-layer dispatcher is untouched. Sections:

- Chat history (max_persisted Input + Clear button)
- Server / Model picker (active server's models shown; pending server
  + model picks applied on Save)
- Custom server URL
- Weather zip code (immediate Apply → dismiss with ``set_zip``)
- Wifi label (immediate Apply → dismiss with ``set_wifi_label``)
- Launcher buttons (Cloud / Senses / Tools / Voice → dismiss with
  ``navigate_to`` so the app re-opens the relevant modal)
- Save / Cancel

Async ``/v1/models`` probing for non-active servers is parked — the
Textual version probes in the background so a user browsing known
servers sees their models live. Qt version shows an explanatory label
until Save is pressed, which triggers the switch.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
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

from tokenpal.config.chatlog_writer import (
    MAX_PERSISTED,
    MIN_PERSISTED,
    clamp_max_persisted,
)
from tokenpal.ui.options_modal import (
    NavigateTo,
    OptionsModalResult,
    OptionsModalState,
    _canon_url,
    _same_server,
)
from tokenpal.ui.qt.modals import _OneShotCallback

log = logging.getLogger(__name__)

_ZIP_LENGTH = 5
_WIFI_LABEL_MAX = 64


class OptionsDialog(QDialog, _OneShotCallback):
    """Umbrella options dialog. ``on_result`` fires exactly once —
    ``OptionsModalResult`` on any save/apply/launcher action,
    ``None`` on cancel / Esc / close."""

    def __init__(
        self,
        state: OptionsModalState,
        on_result: Callable[[OptionsModalResult | None], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Options")
        self.setModal(True)
        self.resize(560, 640)
        self._state = state
        self._on_result = on_result
        self._fired = False
        self._callback_name = "OptionsDialog"

        # Pending picks, applied on Save.
        self._pending_server: str | None = None
        self._pending_model: str | None = None
        self._displayed_server_url: str = (
            _canon_url(state.current_api_url) if state.current_api_url else ""
        )

        layout = QVBoxLayout(self)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_layout = QVBoxLayout(body)

        self._build_chat_history(body_layout)
        self._build_server_model(body_layout)
        self._build_custom_server(body_layout)
        self._build_weather(body_layout)
        self._build_wifi(body_layout)
        self._build_launchers(body_layout)

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

    # --- Section builders -----------------------------------------------

    def _build_chat_history(self, parent: QVBoxLayout) -> None:
        parent.addWidget(_section_header("Chat history"))
        persist_line = (
            "Persist enabled" if self._state.persist_enabled
            else "Persist disabled (edit config.toml to re-enable)"
        )
        parent.addWidget(_section_help(
            f"How many chat entries to remember across restarts "
            f"({MIN_PERSISTED}-{MAX_PERSISTED}). {persist_line}.",
        ))
        self._max_persisted_input = QLineEdit()
        self._max_persisted_input.setValidator(
            QIntValidator(MIN_PERSISTED, MAX_PERSISTED),
        )
        self._max_persisted_input.setText(str(self._state.max_persisted))
        self._max_persisted_input.setMaxLength(6)
        self._max_persisted_input.setPlaceholderText("200")
        parent.addWidget(self._max_persisted_input)

        self._clear_button = QPushButton("Clear history now")
        self._clear_button.clicked.connect(self._on_clear_history)
        parent.addWidget(self._clear_button)

    def _build_server_model(self, parent: QVBoxLayout) -> None:
        parent.addWidget(_section_header("Server / Model"))
        status = (
            f"Active: {self._state.current_api_url}"
            + (f"  —  {self._state.current_model}"
               if self._state.current_model else "")
            if self._state.current_api_url
            else "Pick a server, then a model."
        )
        self._server_status_label = _section_help(status)
        parent.addWidget(self._server_status_label)

        row = QHBoxLayout()
        self._server_list = QListWidget()
        self._server_list.setSelectionMode(
            self._server_list.SelectionMode.SingleSelection,
        )
        for entry in self._state.known_servers:
            model = entry.model or "(no model)"
            marker = "● " if _same_server(
                entry.url, self._state.current_api_url,
            ) else ""
            item = QListWidgetItem(f"{marker}{entry.label}\n  {model}")
            item.setData(Qt.ItemDataRole.UserRole, entry.url)
            self._server_list.addItem(item)
        self._server_list.currentItemChanged.connect(self._on_server_selected)

        self._model_list = QListWidget()
        self._model_list.setSelectionMode(
            self._model_list.SelectionMode.SingleSelection,
        )
        self._populate_model_list()
        self._model_list.currentItemChanged.connect(self._on_model_selected)

        server_col = QVBoxLayout()
        server_col.addWidget(QLabel("Servers"))
        server_col.addWidget(self._server_list, 1)
        model_col = QVBoxLayout()
        self._model_header = QLabel(self._model_header_text())
        model_col.addWidget(self._model_header)
        model_col.addWidget(self._model_list, 1)

        server_wrap = QWidget()
        server_wrap.setLayout(server_col)
        model_wrap = QWidget()
        model_wrap.setLayout(model_col)
        row.addWidget(server_wrap, 1)
        row.addWidget(model_wrap, 1)

        row_widget = QWidget()
        row_widget.setLayout(row)
        parent.addWidget(row_widget)

    def _build_custom_server(self, parent: QVBoxLayout) -> None:
        row = QHBoxLayout()
        self._custom_server_input = QLineEdit()
        self._custom_server_input.setPlaceholderText(
            "Custom URL or host (e.g. 192.168.1.50)",
        )
        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self._on_apply_custom_server)
        row.addWidget(self._custom_server_input, 1)
        row.addWidget(apply_btn)
        parent.addLayout(row)

    def _build_weather(self, parent: QVBoxLayout) -> None:
        parent.addWidget(_section_header("Weather"))
        parent.addWidget(_section_help(
            f"Current: {self._state.weather_label}"
            if self._state.weather_label
            else "No location set. Enter a 5-digit US zip code.",
        ))
        row = QHBoxLayout()
        self._zip_input = QLineEdit()
        self._zip_input.setPlaceholderText("90210")
        self._zip_input.setMaxLength(_ZIP_LENGTH)
        self._zip_input.setValidator(QIntValidator(0, 99999))
        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self._on_apply_zip)
        row.addWidget(self._zip_input, 1)
        row.addWidget(apply_btn)
        parent.addLayout(row)

    def _build_wifi(self, parent: QVBoxLayout) -> None:
        parent.addWidget(_section_header("Wifi label"))
        current = (
            f"Current network labeled '{self._state.current_wifi_label}'. "
            if self._state.current_wifi_label else ""
        )
        parent.addWidget(_section_help(
            current
            + "Give the wifi you're on a friendly name (restart to apply).",
        ))
        row = QHBoxLayout()
        self._wifi_input = QLineEdit()
        self._wifi_input.setPlaceholderText("home / office / coffee-shop")
        self._wifi_input.setMaxLength(_WIFI_LABEL_MAX)
        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self._on_apply_wifi)
        row.addWidget(self._wifi_input, 1)
        row.addWidget(apply_btn)
        parent.addLayout(row)

    def _build_launchers(self, parent: QVBoxLayout) -> None:
        parent.addWidget(_section_header("Settings shortcuts"))
        parent.addWidget(_section_help(
            "Open another settings screen (this modal closes first).",
        ))
        row = QHBoxLayout()
        for label, target in (
            ("Cloud LLM…", "cloud"),
            ("Senses…", "senses"),
            ("Tools…", "tools"),
            ("Voice…", "voice"),
        ):
            btn = QPushButton(label)
            btn.clicked.connect(lambda _=None, t=target: self._on_launch(t))
            row.addWidget(btn)
        parent.addLayout(row)

    # --- Model / server interaction -------------------------------------

    def _populate_model_list(self) -> None:
        self._model_list.clear()
        models = self._models_for_displayed_server()
        if models is None:
            self._model_list.addItem(QListWidgetItem(
                "(switch server to probe its models — save to connect)",
            ))
            self._model_list.item(0).setFlags(Qt.ItemFlag.NoItemFlags)
            return
        if not models:
            self._model_list.addItem(QListWidgetItem("(no models advertised)"))
            self._model_list.item(0).setFlags(Qt.ItemFlag.NoItemFlags)
            return
        active = self._active_model_for(self._displayed_server_url)
        for name in models:
            marker = "● " if name == active else ""
            item = QListWidgetItem(f"{marker}{name}")
            item.setData(Qt.ItemDataRole.UserRole, name)
            self._model_list.addItem(item)

    def _models_for_displayed_server(self) -> tuple[str, ...] | None:
        """The active server's models come pre-populated in state. Any
        other server returns None so we render the "probe on save"
        placeholder."""
        if _same_server(
            self._displayed_server_url, self._state.current_api_url,
        ):
            return tuple(self._state.available_models)
        return None

    def _active_model_for(self, canon: str) -> str:
        if _same_server(canon, self._state.current_api_url):
            return self._state.current_model
        for entry in self._state.known_servers:
            if _same_server(entry.url, canon):
                return entry.model or ""
        return ""

    def _model_header_text(self) -> str:
        if not self._displayed_server_url:
            return "Models"
        return f"Models on {self._displayed_server_url}"

    def _on_server_selected(
        self,
        current: QListWidgetItem | None,
        _prev: QListWidgetItem | None,
    ) -> None:
        if current is None:
            return
        url = current.data(Qt.ItemDataRole.UserRole)
        if not isinstance(url, str):
            return
        self._select_pending_server(url)

    def _on_model_selected(
        self,
        current: QListWidgetItem | None,
        _prev: QListWidgetItem | None,
    ) -> None:
        if current is None:
            return
        name = current.data(Qt.ItemDataRole.UserRole)
        if not isinstance(name, str):
            return
        active = self._active_model_for(self._displayed_server_url)
        self._pending_model = name if name != active else None

    def _select_pending_server(self, url: str) -> None:
        canon = _canon_url(url)
        if _same_server(url, self._state.current_api_url):
            self._pending_server = None
        else:
            self._pending_server = url
        self._pending_model = None
        self._displayed_server_url = canon
        self._model_header.setText(self._model_header_text())
        self._populate_model_list()

    def _on_apply_custom_server(self) -> None:
        url = self._custom_server_input.text().strip()
        if not url:
            return
        self._select_pending_server(url)
        self._server_status_label.setText(
            f"Pending: {url}  —  save to connect.",
        )

    # --- Button handlers ------------------------------------------------

    def _on_save(self) -> None:
        self._deliver(self._on_result, self._collect(clear_history=False))
        self.accept()

    def _on_cancel(self) -> None:
        self._deliver(self._on_result, None)
        self.reject()

    def _on_clear_history(self) -> None:
        self._deliver(self._on_result, self._collect(clear_history=True))
        self.accept()

    def _on_apply_zip(self) -> None:
        raw = self._zip_input.text().strip()
        if not raw:
            return
        self._deliver(
            self._on_result,
            OptionsModalResult(
                max_persisted=self._state.max_persisted, set_zip=raw,
            ),
        )
        self.accept()

    def _on_apply_wifi(self) -> None:
        raw = self._wifi_input.text().strip()
        if not raw:
            return
        self._deliver(
            self._on_result,
            OptionsModalResult(
                max_persisted=self._state.max_persisted, set_wifi_label=raw,
            ),
        )
        self.accept()

    def _on_launch(self, target: str) -> None:
        nav: NavigateTo = target  # type: ignore[assignment]
        self._deliver(
            self._on_result,
            OptionsModalResult(
                max_persisted=self._state.max_persisted,
                clear_history=False,
                navigate_to=nav,
            ),
        )
        self.accept()

    # --- Result assembly ------------------------------------------------

    def _collect(self, *, clear_history: bool) -> OptionsModalResult:
        return OptionsModalResult(
            max_persisted=self._read_max_persisted(),
            clear_history=clear_history,
            navigate_to=None,
            switch_server_to=self._pending_server,
            switch_model_to=self._pending_model,
        )

    def _read_max_persisted(self) -> int:
        raw = self._max_persisted_input.text().strip()
        if not raw:
            return self._state.max_persisted
        try:
            return clamp_max_persisted(int(raw))
        except ValueError:
            # The QIntValidator should make this unreachable, but keep
            # the fallback so a weird paste + programmatic setText
            # doesn't crash Save.
            return self._state.max_persisted


def _section_header(text: str) -> QLabel:
    label = QLabel(f"<b>{text}</b>")
    label.setStyleSheet("color: #4ade80")
    return label


def _section_help(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("color: #888")
    label.setWordWrap(True)
    return label
