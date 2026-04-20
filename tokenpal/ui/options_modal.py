"""Modal for /options — umbrella settings screen.

Exposes:
  - Chat history: digit-only Input for max_persisted + "Clear history now"
    Button (propagates a flag in the result; app layer wipes the chat_log
    table via MemoryStore.clear_chat_log()).
  - Settings shortcuts: launcher Buttons for "Cloud LLM...", "Senses
    toggles...", "Tools...". Each dismisses the modal with ``navigate_to``
    set so the app-layer callback opens the matching existing modal via
    its existing helper (``_open_cloud_modal``, ``_open_senses_modal``,
    ``_open_tools_modal``) — no logic duplicated.

Sanitization: the max_persisted Input uses ``type="integer"`` plus
``restrict=r"[0-9]*"`` so typed non-digits are rejected at the widget.
``_collect()`` re-casts with int() inside try/except and clamps via
chatlog_writer.clamp_max_persisted before handing the value to the app.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Literal

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label

from tokenpal.config.chatlog_writer import (
    MAX_PERSISTED,
    MIN_PERSISTED,
    clamp_max_persisted,
)

NavigateTo = Literal["cloud", "senses", "tools", "voice"]


def _same_server(a: str, b: str) -> bool:
    """Compare two api URLs with the same normalization /server switch uses."""

    def _norm(u: str) -> str:
        u = u.strip().rstrip("/")
        if u and not u.endswith("/v1"):
            u += "/v1"
        return u

    return bool(a) and bool(b) and _norm(a) == _norm(b)


@dataclass(frozen=True)
class ServerEntry:
    """One row in the Server section's known-servers list."""

    url: str           # canonical URL used for switching
    label: str         # short display label (e.g. "local", "remote", host)
    model: str | None  # remembered model, or None if we haven't seen one


@dataclass(frozen=True)
class OptionsModalState:
    """Current state fed into the modal."""

    max_persisted: int
    persist_enabled: bool
    current_api_url: str = ""
    known_servers: tuple[ServerEntry, ...] = field(default_factory=tuple)
    current_model: str = ""
    available_models: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class OptionsModalResult:
    """Result from the modal. ``navigate_to`` non-None means the user
    clicked a launcher button and the app should open that modal next
    (any max_persisted edit is discarded in that case).
    ``clear_history`` True means the user clicked Clear; the app wipes
    both the persisted table and the live widget.
    ``switch_server_to`` non-None means the user picked a different
    server; the app re-dispatches through ``/server switch <value>``.
    """

    max_persisted: int
    clear_history: bool = False
    navigate_to: NavigateTo | None = None
    switch_server_to: str | None = None
    switch_model_to: str | None = None


class OptionsModal(ModalScreen[OptionsModalResult | None]):
    """Umbrella options modal. Save returns an OptionsModalResult; Cancel /
    Esc returns None."""

    DEFAULT_CSS: ClassVar[str] = """
    OptionsModal {
        align: center middle;
    }
    OptionsModal #options-body {
        width: 70%;
        min-width: 40;
        max-width: 120;
        height: auto;
        max-height: 90%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    OptionsModal Label {
        width: 100%;
        height: auto;
    }
    OptionsModal #options-title {
        text-style: bold;
        padding-bottom: 1;
    }
    OptionsModal .section-header {
        text-style: bold;
        color: $accent;
        padding-top: 1;
    }
    OptionsModal .section-help {
        color: $text-muted;
        padding-bottom: 1;
        padding-left: 2;
    }
    OptionsModal Input {
        margin-bottom: 1;
        width: 100%;
    }
    OptionsModal .launcher-row {
        height: auto;
        width: 100%;
        padding-top: 1;
    }
    OptionsModal #options-buttons {
        height: auto;
        width: 100%;
        padding-top: 1;
        align-horizontal: right;
    }
    OptionsModal Button {
        margin-left: 1;
    }
    OptionsModal .server-row {
        height: auto;
        width: 100%;
        padding-top: 0;
    }
    OptionsModal .server-row Button {
        margin-left: 0;
        margin-right: 1;
        width: 100%;
    }
    OptionsModal .server-row Button.active {
        text-style: bold;
    }
    OptionsModal #custom-server-row {
        height: auto;
        width: 100%;
        padding-top: 1;
    }
    OptionsModal #custom-server-input {
        width: 1fr;
        margin-right: 1;
        margin-bottom: 0;
    }
    """

    BINDINGS: ClassVar[
        list[Binding | tuple[str, str] | tuple[str, str, str]]
    ] = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(self, state: OptionsModalState) -> None:
        super().__init__()
        self._state = state
        self._pending_model: str | None = None

    def compose(self) -> ComposeResult:
        s = self._state
        with VerticalScroll(id="options-body"):
            yield Label("Options", id="options-title")

            # --------------------------------------------------------------
            # Chat history section
            # --------------------------------------------------------------
            yield Label("Chat history", classes="section-header")
            persist_line = (
                "Persist enabled" if s.persist_enabled
                else "Persist disabled (edit config.toml to re-enable)"
            )
            yield Label(
                f"How many chat entries to remember across restarts "
                f"({MIN_PERSISTED}-{MAX_PERSISTED}). {persist_line}.",
                classes="section-help",
            )
            yield Input(
                value=str(s.max_persisted),
                placeholder="200",
                id="max-persisted-input",
                type="integer",
                restrict=r"[0-9]*",
                max_length=6,
            )
            with Horizontal(classes="launcher-row"):
                yield Button(
                    "Clear history now",
                    id="clear-history-btn",
                    variant="warning",
                )

            # --------------------------------------------------------------
            # Server section — pick an inference server to connect to.
            # --------------------------------------------------------------
            yield Label("Server", classes="section-header")
            if s.current_api_url:
                yield Label(
                    f"Active: {s.current_api_url}",
                    classes="section-help",
                )
            else:
                yield Label(
                    "Pick a server below, or enter a custom URL.",
                    classes="section-help",
                )
            for i, entry in enumerate(s.known_servers):
                model_col = entry.model or "(no model)"
                label = f"{entry.label}  —  {entry.url}  —  {model_col}"
                is_active = _same_server(entry.url, s.current_api_url)
                if is_active:
                    label = "● " + label
                btn = Button(
                    label,
                    id=f"server-row-{i}",
                    variant="primary" if is_active else "default",
                    disabled=is_active,
                )
                if is_active:
                    btn.add_class("active")
                with Horizontal(classes="server-row"):
                    yield btn
            with Horizontal(id="custom-server-row"):
                yield Input(
                    placeholder="Custom URL or host (e.g. 192.168.1.50)",
                    id="custom-server-input",
                )
                yield Button("Apply", id="custom-server-btn")

            # --------------------------------------------------------------
            # Models section — pick a model served by the active server.
            # --------------------------------------------------------------
            yield Label("Models (on this server)", classes="section-header")
            if not s.available_models:
                hint = (
                    f"Active: {s.current_model} (probing server…)"
                    if s.current_model
                    else "Connecting to server — model list unavailable."
                )
                yield Label(hint, classes="section-help")
            else:
                yield Label(
                    f"Active: {s.current_model}" if s.current_model
                    else "Pick a model.",
                    classes="section-help",
                )
                for i, name in enumerate(s.available_models):
                    is_active = name == s.current_model
                    label = ("● " if is_active else "") + name
                    btn = Button(
                        label,
                        id=f"model-row-{i}",
                        variant="primary" if is_active else "default",
                        disabled=is_active,
                    )
                    if is_active:
                        btn.add_class("active")
                    with Horizontal(classes="server-row"):
                        yield btn

            # --------------------------------------------------------------
            # Settings shortcuts — launchers for existing modals
            # --------------------------------------------------------------
            yield Label("Settings shortcuts", classes="section-header")
            yield Label(
                "Open another settings screen (this modal closes first).",
                classes="section-help",
            )
            with Horizontal(classes="launcher-row"):
                yield Button("Cloud LLM...", id="launch-cloud-btn")
                yield Button("Senses...", id="launch-senses-btn")
                yield Button("Tools...", id="launch-tools-btn")
                yield Button("Voice...", id="launch-voice-btn")

            # --------------------------------------------------------------
            # Bottom save / cancel
            # --------------------------------------------------------------
            with Horizontal(id="options-buttons"):
                yield Button("Cancel", id="cancel-btn", variant="default")
                yield Button("Save", id="save-btn", variant="primary")

    def on_mount(self) -> None:
        # Focus the topmost editable widget so the scroll view stays at the
        # top; focusing Save would yank it to the bottom.
        try:
            self.query_one("#max-persisted-input", Input).focus()
        except Exception:
            pass
        self.query_one("#options-body", VerticalScroll).scroll_home(
            animate=False
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id == "save-btn":
            self.dismiss(self._collect(clear_history=False))
        elif btn_id == "cancel-btn":
            self.dismiss(None)
        elif btn_id == "clear-history-btn":
            # Clear is an explicit user action; persist the current input
            # value too so saving a new max_persisted + clearing in one shot
            # works as expected.
            self.dismiss(self._collect(clear_history=True))
        elif btn_id == "launch-cloud-btn":
            self.dismiss(self._nav("cloud"))
        elif btn_id == "launch-senses-btn":
            self.dismiss(self._nav("senses"))
        elif btn_id == "launch-tools-btn":
            self.dismiss(self._nav("tools"))
        elif btn_id == "launch-voice-btn":
            self.dismiss(self._nav("voice"))
        elif btn_id == "custom-server-btn":
            self._apply_custom_server()
        elif btn_id.startswith("server-row-"):
            try:
                idx = int(btn_id.removeprefix("server-row-"))
            except ValueError:
                return
            if 0 <= idx < len(self._state.known_servers):
                entry = self._state.known_servers[idx]
                self.dismiss(self._switch(entry.url))
        elif btn_id.startswith("model-row-"):
            try:
                idx = int(btn_id.removeprefix("model-row-"))
            except ValueError:
                return
            if 0 <= idx < len(self._state.available_models):
                name = self._state.available_models[idx]
                self._select_pending_model(name)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _collect(self, *, clear_history: bool) -> OptionsModalResult:
        pending = self._pending_model
        current = self._state.current_model
        switch_to = pending if pending and pending != current else None
        return OptionsModalResult(
            max_persisted=self._read_max_persisted(),
            clear_history=clear_history,
            navigate_to=None,
            switch_model_to=switch_to,
        )

    def _select_pending_model(self, name: str) -> None:
        """Mark a model row as the pending pick. Applied on Save."""
        self._pending_model = name
        current = self._state.current_model
        for i, candidate in enumerate(self._state.available_models):
            try:
                btn = self.query_one(f"#model-row-{i}", Button)
            except Exception:
                continue
            is_current = candidate == current
            is_pending = candidate == name and not is_current
            if is_current:
                label = "● " + candidate
                btn.variant = "primary"
                btn.disabled = True
                btn.add_class("active")
            elif is_pending:
                label = "○ " + candidate + "  (save to apply)"
                btn.variant = "success"
                btn.disabled = False
                btn.remove_class("active")
            else:
                label = candidate
                btn.variant = "default"
                btn.disabled = False
                btn.remove_class("active")
            btn.label = label

    def _nav(self, target: NavigateTo) -> OptionsModalResult:
        # Navigate-only: carry the current (unedited) max_persisted so the
        # app layer doesn't accidentally clobber the stored value with 0.
        return OptionsModalResult(
            max_persisted=self._state.max_persisted,
            clear_history=False,
            navigate_to=target,
        )

    def _switch(self, url: str) -> OptionsModalResult:
        return OptionsModalResult(
            max_persisted=self._state.max_persisted,
            clear_history=False,
            navigate_to=None,
            switch_server_to=url,
        )

    def _apply_custom_server(self) -> None:
        try:
            raw = self.query_one("#custom-server-input", Input).value.strip()
        except Exception:
            return
        if not raw:
            return
        self.dismiss(self._switch(raw))

    def _read_max_persisted(self) -> int:
        try:
            raw = self.query_one("#max-persisted-input", Input).value.strip()
        except Exception:
            return self._state.max_persisted
        if not raw:
            return self._state.max_persisted
        try:
            parsed = int(raw)
        except ValueError:
            # restrict=r"[0-9]*" should make this unreachable; belt-and-suspenders.
            return self._state.max_persisted
        return clamp_max_persisted(parsed)
