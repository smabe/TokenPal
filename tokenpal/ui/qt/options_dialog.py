"""Qt port of the /options umbrella modal.

Mirrors the Textual version (tokenpal/ui/options_modal.py) — same
``OptionsModalState`` input, same ``OptionsModalResult`` output — so the
app-layer dispatcher is untouched. Sections:

- Chat history (max_persisted Input + Clear button)
- Server / Model picker (active server's models shown; pending server
  + model picks applied on Save)
- Custom server URL
- Weather zip code (Apply fires ``set_zip`` immediately; dialog stays open)
- Wifi label (Apply fires ``set_wifi_label`` immediately; dialog stays open)
- Launcher buttons (Cloud / Senses / Tools / Voice → invoke
  ``on_open_subdialog`` so the overlay opens the target as a sibling
  window without closing this one; pending picks stay pending)
- Save / Cancel

Async ``/v1/models`` probing for non-active servers is parked — the
Textual version probes in the background so a user browsing known
servers sees their models live. Qt version shows an explanatory label
until Save is pressed, which triggers the switch.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFontComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from tokenpal.config.chatlog_writer import (
    MAX_FONT_SIZE,
    MAX_PERSISTED,
    MIN_FONT_SIZE,
    MIN_PERSISTED,
    clamp_background_opacity,
    clamp_font_size,
    clamp_max_persisted,
)
from tokenpal.config.schema import FontConfig
from tokenpal.ui.options_modal import (
    NavigateTo,
    OptionsModalResult,
    OptionsModalState,
    _canon_url,
    _same_server,
)
from tokenpal.ui.qt._text_fx import qt_font_from_config
from tokenpal.ui.qt.modals import _OneShotCallback

log = logging.getLogger(__name__)

_ZIP_LENGTH = 5
_WIFI_LABEL_MAX = 64


class OptionsDialog(QDialog, _OneShotCallback):
    """Umbrella options dialog. ``on_result`` may fire multiple times:
    once per in-dialog Apply (zip / wifi partial results), then exactly
    once on Save (full result) or Cancel/Esc/close (``None``). Once the
    terminal Save/Cancel has fired, further invocations are suppressed."""

    def __init__(
        self,
        state: OptionsModalState,
        on_result: Callable[[OptionsModalResult | None], None],
        parent: QWidget | None = None,
        on_opacity_preview: Callable[[float], None] | None = None,
        on_open_subdialog: Callable[[NavigateTo], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Options")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.resize(560, 640)
        self._state = state
        self._on_result = on_result
        self._on_opacity_preview = on_opacity_preview
        self._on_open_subdialog = on_open_subdialog
        self._initial_opacity = clamp_background_opacity(
            state.chat_history_opacity,
        )
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
        self._chat_font_widgets = self._build_font_group(
            body_layout, "Chat font", state.chat_font,
        )
        self._bubble_font_widgets = self._build_font_group(
            body_layout, "Speech bubble font", state.bubble_font,
        )
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

        initial_pct = int(round(
            clamp_background_opacity(self._state.chat_history_opacity) * 100,
        ))
        self._opacity_label = QLabel(
            f"Background opacity: {initial_pct}%",
        )
        self._opacity_label.setStyleSheet("color: #888")
        parent.addWidget(self._opacity_label)
        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(0, 100)
        self._opacity_slider.setValue(initial_pct)
        self._opacity_slider.valueChanged.connect(self._on_opacity_changed)
        parent.addWidget(self._opacity_slider)

    def _build_font_group(
        self, parent: QVBoxLayout, title: str, initial: FontConfig,
    ) -> _FontGroupWidgets:
        """Compose a font picker: family combo, size spinner, bold/italic/
        underline checkboxes, and a live preview. Returns the widget bundle
        so the save path can read back the values."""
        parent.addWidget(_section_header(title))
        parent.addWidget(_section_help(
            "Pick a family installed on this machine. Size 8 - 48. "
            "Leave family empty for the default.",
        ))
        family = QFontComboBox()
        if initial.family:
            family.setCurrentText(initial.family)
        parent.addWidget(family)

        row = QHBoxLayout()
        size = QSpinBox()
        size.setRange(MIN_FONT_SIZE, MAX_FONT_SIZE)
        size.setValue(
            clamp_font_size(initial.size_pt) if initial.size_pt > 0 else 13,
        )
        size.setSuffix(" pt")
        row.addWidget(QLabel("Size"))
        row.addWidget(size)
        bold = QCheckBox("Bold")
        bold.setChecked(initial.bold)
        italic = QCheckBox("Italic")
        italic.setChecked(initial.italic)
        underline = QCheckBox("Underline")
        underline.setChecked(initial.underline)
        for cb in (bold, italic, underline):
            row.addWidget(cb)
        row.addStretch(1)
        row_widget = QWidget()
        row_widget.setLayout(row)
        parent.addWidget(row_widget)

        preview = QLabel("The quick brown fox jumps over the lazy dog.")
        preview.setStyleSheet("color: #ccc; padding: 4px 0;")
        parent.addWidget(preview)

        widgets = _FontGroupWidgets(
            family=family, size=size, bold=bold, italic=italic,
            underline=underline, preview=preview, initial=initial,
        )
        # Snapshot the widget state AFTER Qt has populated combo boxes and
        # honored our .setValue calls. Comparing user-visible state against
        # this baseline avoids a false "changed" diff on the first open,
        # where QFontComboBox auto-picks an arbitrary family even when the
        # stored config is empty.
        widgets.baseline = widgets.read()

        def refresh_preview() -> None:
            preview.setFont(qt_font_from_config(widgets.read()))
        family.currentFontChanged.connect(lambda _f: refresh_preview())
        size.valueChanged.connect(lambda _v: refresh_preview())
        for cb in (bold, italic, underline):
            cb.toggled.connect(lambda _t: refresh_preview())
        refresh_preview()
        return widgets

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
        self._zip_status = QLabel("")
        self._zip_status.setStyleSheet("color: #888")
        parent.addWidget(self._zip_status)

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
        self._wifi_status = QLabel("")
        self._wifi_status.setStyleSheet("color: #888")
        parent.addWidget(self._wifi_status)

    def _build_launchers(self, parent: QVBoxLayout) -> None:
        parent.addWidget(_section_header("Settings shortcuts"))
        parent.addWidget(_section_help(
            "Open another settings window alongside this one. "
            "Pending picks here stay pending until you hit Save.",
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

    def _on_opacity_changed(self, value: int) -> None:
        self._opacity_label.setText(f"Background opacity: {value}%")
        if self._on_opacity_preview is not None:
            self._on_opacity_preview(
                clamp_background_opacity(value / 100.0),
            )

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
        if self._on_opacity_preview is not None:
            self._on_opacity_preview(self._initial_opacity)
        self._deliver(self._on_result, None)
        self.reject()

    def _on_clear_history(self) -> None:
        self._deliver(self._on_result, self._collect(clear_history=True))
        self.accept()

    def _on_apply_zip(self) -> None:
        raw = self._zip_input.text().strip()
        if not raw:
            return
        self._deliver_partial(OptionsModalResult(
            max_persisted=self._state.max_persisted, set_zip=raw,
        ))
        self._zip_status.setText(f"Applied: {raw}")
        self._zip_input.clear()

    def _on_apply_wifi(self) -> None:
        raw = self._wifi_input.text().strip()
        if not raw:
            return
        self._deliver_partial(OptionsModalResult(
            max_persisted=self._state.max_persisted, set_wifi_label=raw,
        ))
        self._wifi_status.setText(f"Applied: {raw}")
        self._wifi_input.clear()

    def _deliver_partial(self, result: OptionsModalResult) -> None:
        """Fire ``on_result`` without arming the one-shot guard, so later
        Apply clicks (and the terminal Save) still reach the app layer."""
        if getattr(self, "_fired", False):
            return
        try:
            self._on_result(result)
        except Exception:
            log.exception("%s partial callback raised", self._callback_name)

    def _on_launch(self, target: str) -> None:
        nav: NavigateTo = target  # type: ignore[assignment]
        if self._on_open_subdialog is not None:
            self._on_open_subdialog(nav)

    # --- Result assembly ------------------------------------------------

    def _collect(self, *, clear_history: bool) -> OptionsModalResult:
        return OptionsModalResult(
            max_persisted=self._read_max_persisted(),
            clear_history=clear_history,
            navigate_to=None,
            switch_server_to=self._pending_server,
            switch_model_to=self._pending_model,
            set_chat_history_opacity=self._read_opacity(),
            set_chat_font=self._read_font_if_changed(self._chat_font_widgets),
            set_bubble_font=self._read_font_if_changed(self._bubble_font_widgets),
        )

    def _read_font_if_changed(
        self, w: _FontGroupWidgets,
    ) -> FontConfig | None:
        """Return the current widget values only if they differ from the
        baseline captured at dialog construction time."""
        current = w.read()
        if w.baseline is not None and current == w.baseline:
            return None
        return current

    def _read_opacity(self) -> float:
        return clamp_background_opacity(
            self._opacity_slider.value() / 100.0,
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


@dataclass
class _FontGroupWidgets:
    family: QFontComboBox
    size: QSpinBox
    bold: QCheckBox
    italic: QCheckBox
    underline: QCheckBox
    preview: QLabel
    initial: FontConfig
    baseline: FontConfig | None = None  # snapshotted after widgets render

    def read(self) -> FontConfig:
        # Empty currentText means "use Qt's default" — preserve that as
        # empty family so downstream callers pick their own fallback.
        family_text = self.family.currentText().strip()
        return FontConfig(
            family=family_text,
            size_pt=clamp_font_size(self.size.value()),
            bold=self.bold.isChecked(),
            italic=self.italic.isChecked(),
            underline=self.underline.isChecked(),
        )


def _section_header(text: str) -> QLabel:
    label = QLabel(f"<b>{text}</b>")
    label.setStyleSheet("color: #4ade80")
    return label


def _section_help(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("color: #888")
    label.setWordWrap(True)
    return label
