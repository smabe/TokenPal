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
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label

from tokenpal.config.chatlog_writer import (
    MAX_PERSISTED,
    MIN_PERSISTED,
    clamp_max_persisted,
)

NavigateTo = Literal["cloud", "senses", "tools", "voice"]


def _canon_url(u: str) -> str:
    """Normalize an inference URL: strip trailing slash, append /v1 when missing."""
    u = u.strip().rstrip("/")
    if u and not u.endswith("/v1"):
        u += "/v1"
    return u


def _same_server(a: str, b: str) -> bool:
    """Compare two api URLs with the same normalization /server switch uses."""
    return bool(a) and bool(b) and _canon_url(a) == _canon_url(b)


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
    weather_label: str = ""
    current_wifi_label: str = ""


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
    set_zip: str | None = None
    set_wifi_label: str | None = None


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
    OptionsModal #server-model-row {
        layout: horizontal;
        height: auto;
        width: 100%;
        padding-top: 0;
    }
    OptionsModal #server-col,
    OptionsModal #model-col {
        width: 1fr;
        height: auto;
        padding: 0 1;
    }
    OptionsModal #server-col Button,
    OptionsModal #model-col Button {
        width: 100%;
        margin: 0 0 1 0;
    }
    OptionsModal .col-header {
        text-style: bold;
        color: $accent;
        padding-bottom: 1;
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
    OptionsModal #zip-row,
    OptionsModal #wifi-row {
        height: auto;
        width: 100%;
        padding-top: 1;
    }
    OptionsModal #zip-input,
    OptionsModal #wifi-input {
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
        # Pending picks — applied on Save, discarded on Cancel/Esc.
        self._pending_server: str | None = None
        self._pending_model: str | None = None
        # Which server's models are shown in the right column right now.
        # Defaults to the active server so the initial render mirrors what
        # the app already knows via state.available_models.
        self._displayed_server_url: str = (
            _canon_url(state.current_api_url) if state.current_api_url else ""
        )
        # Cached /v1/models probes for non-active servers. None sentinel
        # means "probe in flight"; () means "probe failed / empty".
        self._probed_models: dict[str, tuple[str, ...] | None] = {}
        if state.current_api_url:
            self._probed_models[_canon_url(state.current_api_url)] = tuple(
                state.available_models
            )

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
            # Server + Model picker — servers on the left, models for the
            # currently-selected server on the right. Clicking a server row
            # re-populates the right column; clicking a model marks a
            # pending pick. Both apply on Save.
            # --------------------------------------------------------------
            yield Label("Server / Model", classes="section-header")
            status = (
                f"Active: {s.current_api_url}"
                + (f"  —  {s.current_model}" if s.current_model else "")
                if s.current_api_url
                else "Pick a server, then a model."
            )
            yield Label(status, classes="section-help", id="server-model-status")
            with Horizontal(id="server-model-row"):
                with Vertical(id="server-col"):
                    yield Label("Servers", classes="col-header")
                    for i, _entry in enumerate(s.known_servers):
                        yield Button(
                            self._server_label_text(i),
                            id=f"server-row-{i}",
                            variant=self._server_variant(i),
                            disabled=False,
                        )
                with Vertical(id="model-col"):
                    yield Label(
                        self._model_col_header_text(),
                        classes="col-header",
                        id="model-col-header",
                    )
                    # Initial model buttons mirror the active server's list
                    # (we primed _probed_models with state.available_models).
                    yield from self._build_model_widgets()
            with Horizontal(id="custom-server-row"):
                yield Input(
                    placeholder="Custom URL or host (e.g. 192.168.1.50)",
                    id="custom-server-input",
                )
                yield Button("Apply", id="custom-server-btn")

            # --------------------------------------------------------------
            # Weather section — set location via US zip code.
            # --------------------------------------------------------------
            yield Label("Weather", classes="section-header")
            if s.weather_label:
                yield Label(
                    f"Current: {s.weather_label}",
                    classes="section-help",
                )
            else:
                yield Label(
                    "No location set. Enter a 5-digit US zip code.",
                    classes="section-help",
                )
            with Horizontal(id="zip-row"):
                yield Input(
                    placeholder="90210",
                    id="zip-input",
                    restrict=r"[0-9]*",
                    max_length=5,
                )
                yield Button("Apply", id="zip-btn")

            # --------------------------------------------------------------
            # Wifi label section — label the currently-connected SSID.
            # --------------------------------------------------------------
            yield Label("Wifi label", classes="section-header")
            if s.current_wifi_label:
                current_line = f"Current network labeled '{s.current_wifi_label}'. "
            else:
                current_line = ""
            yield Label(
                current_line
                + "Give the wifi you're on a friendly name (restart to apply).",
                classes="section-help",
            )
            with Horizontal(id="wifi-row"):
                yield Input(
                    placeholder="home / office / coffee-shop",
                    id="wifi-input",
                    max_length=64,
                )
                yield Button("Apply", id="wifi-btn")

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

    async def on_button_pressed(self, event: Button.Pressed) -> None:
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
            await self._apply_custom_server()
        elif btn_id == "zip-btn":
            self._apply_zip()
        elif btn_id == "wifi-btn":
            self._apply_wifi()
        elif btn_id.startswith("server-row-"):
            try:
                idx = int(btn_id.removeprefix("server-row-"))
            except ValueError:
                return
            if 0 <= idx < len(self._state.known_servers):
                entry = self._state.known_servers[idx]
                await self._select_pending_server(entry.url)
        elif btn_id.startswith("model-row-"):
            try:
                idx = int(btn_id.removeprefix("model-row-"))
            except ValueError:
                return
            displayed = self._displayed_models()
            if displayed is not None and 0 <= idx < len(displayed):
                await self._select_pending_model(displayed[idx])

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _collect(self, *, clear_history: bool) -> OptionsModalResult:
        return OptionsModalResult(
            max_persisted=self._read_max_persisted(),
            clear_history=clear_history,
            navigate_to=None,
            switch_server_to=self._pending_server,
            switch_model_to=self._pending_model,
        )

    # ------------------------------------------------------------------
    # Server / Model picker — internal state + render helpers
    # ------------------------------------------------------------------

    def _displayed_models(self) -> tuple[str, ...] | None:
        """Return the model list for the displayed server, or None if a
        probe is in flight (used to render a "(probing…)" placeholder)."""
        return self._probed_models.get(self._displayed_server_url)

    def _active_model_for(self, canon: str) -> str:
        """The 'remembered active' model for *canon*. For the current
        server that's state.current_model; for known non-current servers
        we use whatever ServerEntry.model recorded last time."""
        if _same_server(canon, self._state.current_api_url):
            return self._state.current_model
        for entry in self._state.known_servers:
            if _same_server(entry.url, canon):
                return entry.model or ""
        return ""

    def _server_display_label(self, url: str) -> str:
        """Short label for a server URL — entry.label when known, else the URL."""
        for entry in self._state.known_servers:
            if _same_server(entry.url, url):
                return entry.label
        return url

    def _server_label_text(self, idx: int) -> str:
        entry = self._state.known_servers[idx]
        canon = _canon_url(entry.url)
        marker = ""
        if _same_server(entry.url, self._state.current_api_url):
            marker = "● "
        elif self._pending_server and _same_server(entry.url, self._pending_server):
            marker = "○ "
        elif canon == self._displayed_server_url:
            marker = "› "
        model = entry.model or "(no model)"
        return f"{marker}{entry.label}\n{model}"

    def _server_variant(self, idx: int) -> str:
        entry = self._state.known_servers[idx]
        if _same_server(entry.url, self._state.current_api_url):
            return "primary"
        if self._pending_server and _same_server(entry.url, self._pending_server):
            return "success"
        return "default"

    def _model_col_header_text(self) -> str:
        if not self._displayed_server_url:
            return "Models"
        return f"Models on {self._server_display_label(self._displayed_server_url)}"

    def _build_model_widgets(self) -> list[Label | Button]:
        """Compose-time helper: yield the initial model column widgets."""
        widgets: list[Label | Button] = []
        models = self._displayed_models()
        active = self._active_model_for(self._displayed_server_url)
        if models is None:
            widgets.append(Label("(probing…)", classes="section-help"))
            return widgets
        if not models:
            widgets.append(
                Label("(no models advertised)", classes="section-help"),
            )
            return widgets
        for i, name in enumerate(models):
            is_current = name == active
            widgets.append(
                Button(
                    self._model_label_text(name, is_current, is_pending=False),
                    id=f"model-row-{i}",
                    variant="primary" if is_current else "default",
                    disabled=is_current,
                ),
            )
        return widgets

    def _model_label_text(
        self, name: str, is_current: bool, *, is_pending: bool,
    ) -> str:
        if is_current:
            return "● " + name
        if is_pending:
            return "○ " + name + "  (save to apply)"
        return name

    async def _select_pending_server(self, url: str) -> None:
        """Mark a server row as the pending pick + repopulate the model
        column to show that server's models. Applied on Save."""
        canon = _canon_url(url)
        if _same_server(url, self._state.current_api_url):
            self._pending_server = None
        else:
            self._pending_server = url
        # Picking a different server invalidates any in-flight model pick —
        # the old name probably doesn't exist on the new server.
        self._pending_model = None
        self._displayed_server_url = canon
        self._refresh_server_col()
        await self._refresh_model_col()
        if canon not in self._probed_models:
            self.run_worker(
                self._probe_models_worker(canon),
                group="probe-models",
                exclusive=False,
            )

    async def _select_pending_model(self, name: str) -> None:
        """Mark a model row as the pending pick. Applied on Save."""
        active = self._active_model_for(self._displayed_server_url)
        self._pending_model = name if name != active else None
        await self._refresh_model_col()

    def _refresh_server_col(self) -> None:
        """Re-style server-col buttons to reflect current/pending state."""
        for i in range(len(self._state.known_servers)):
            try:
                btn = self.query_one(f"#server-row-{i}", Button)
            except Exception:
                continue
            btn.label = self._server_label_text(i)
            btn.variant = self._server_variant(i)

    async def _refresh_model_col(self) -> None:
        """Tear down and rebuild the model column for the displayed server.

        Awaited removal is load-bearing: ``widget.remove()`` is async and
        only queues the drop. Without the await, mounting the new buttons
        with the same ``model-row-N`` ids races against the queued
        removal and Textual raises DuplicateIds.
        """
        try:
            col = self.query_one("#model-col", Vertical)
            header = self.query_one("#model-col-header", Label)
        except Exception:
            return
        header.update(self._model_col_header_text())
        to_remove = [c for c in col.children if c is not header]
        if to_remove:
            await col.remove_children(to_remove)
        new_widgets = self._build_model_widgets()
        if new_widgets:
            await col.mount_all(new_widgets)

    async def _probe_models_worker(self, canon: str) -> None:
        """Background fetch of /v1/models for *canon*. Best-effort: a
        failure caches an empty tuple so we don't re-probe in a loop."""
        if (
            canon in self._probed_models
            and self._probed_models[canon] is not None
        ):
            return
        self._probed_models[canon] = None  # in-flight marker
        if self._displayed_server_url == canon:
            await self._refresh_model_col()
        ids: tuple[str, ...] = ()
        try:
            import httpx

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{canon}/models")
                resp.raise_for_status()
                data = resp.json()
                ids = tuple(
                    str(m.get("id", ""))
                    for m in data.get("data", [])
                    if m.get("id")
                )
        except Exception:
            ids = ()
        self._probed_models[canon] = ids
        if self._displayed_server_url == canon:
            await self._refresh_model_col()

    def _nav(self, target: NavigateTo) -> OptionsModalResult:
        # Navigate-only: carry the current (unedited) max_persisted so the
        # app layer doesn't accidentally clobber the stored value with 0.
        return OptionsModalResult(
            max_persisted=self._state.max_persisted,
            clear_history=False,
            navigate_to=target,
        )

    async def _apply_custom_server(self) -> None:
        try:
            raw = self.query_one("#custom-server-input", Input).value.strip()
        except Exception:
            return
        if not raw:
            return
        await self._select_pending_server(raw)
        # Surface the in-flight pick in the status line so the user knows
        # Save is needed to actually connect (the custom URL has no row in
        # the server-col).
        try:
            self.query_one(
                "#server-model-status", Label,
            ).update(f"Pending: {raw}  —  save to connect.")
        except Exception:
            pass

    def _apply_zip(self) -> None:
        try:
            raw = self.query_one("#zip-input", Input).value.strip()
        except Exception:
            return
        if not raw:
            return
        self.dismiss(
            OptionsModalResult(
                max_persisted=self._state.max_persisted,
                set_zip=raw,
            )
        )

    def _apply_wifi(self) -> None:
        try:
            raw = self.query_one("#wifi-input", Input).value.strip()
        except Exception:
            return
        if not raw:
            return
        self.dismiss(
            OptionsModalResult(
                max_persisted=self._state.max_persisted,
                set_wifi_label=raw,
            )
        )

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
