"""Modal for /cloud - manage Anthropic-backed research synth.

Exposes in one screen:
  - API key text input (only visible when no key is stored; shows a
    "replace stored key" toggle otherwise so the existing key isn't
    echoed)
  - Three checkboxes: enable cloud, use for synth, use for planner
  - Radio group for model (haiku / sonnet / opus)

Pure UI: the modal dismisses with a ``CloudModalResult`` dataclass;
the caller (tokenpal/app.py) persists the changes via secrets.py and
cloud_writer.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    Input,
    Label,
    RadioButton,
    RadioSet,
)

from tokenpal.llm.cloud_backend import ALLOWED_MODELS


@dataclass(frozen=True)
class CloudModalState:
    """Current state fed into the modal."""

    enabled: bool
    research_synth: bool
    research_plan: bool
    model: str
    key_fingerprint: str | None  # None when no key stored


@dataclass(frozen=True)
class CloudModalResult:
    """Result from the modal. ``new_api_key`` is None when the user did
    not enter (or asked to replace) a key; the caller keeps whatever is
    on disk already."""

    enabled: bool
    research_synth: bool
    research_plan: bool
    model: str
    new_api_key: str | None


class CloudModal(ModalScreen[CloudModalResult | None]):
    """Cloud-LLM settings modal. Submits a CloudModalResult on save,
    None on cancel (Esc or Cancel button)."""

    DEFAULT_CSS: ClassVar[str] = """
    CloudModal {
        align: center middle;
    }
    CloudModal #cloud-body {
        width: 72;
        max-width: 90%;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    CloudModal #cloud-title {
        text-style: bold;
        padding-bottom: 1;
    }
    CloudModal .section-header {
        text-style: bold;
        color: $accent;
        padding-top: 1;
    }
    CloudModal .section-help {
        color: $text-muted;
        padding-bottom: 1;
    }
    CloudModal Input {
        margin-bottom: 1;
    }
    CloudModal RadioSet {
        height: auto;
        border: none;
        padding: 0;
    }
    CloudModal #cloud-buttons {
        height: auto;
        padding-top: 1;
        align-horizontal: right;
    }
    CloudModal Button {
        margin-left: 1;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(self, state: CloudModalState) -> None:
        super().__init__()
        self._state = state

    def compose(self) -> ComposeResult:
        s = self._state
        with VerticalScroll(id="cloud-body"):
            yield Label("Cloud LLM", id="cloud-title")

            yield Label("API Key", classes="section-header")
            if s.key_fingerprint is not None:
                yield Label(
                    f"  Key stored: {s.key_fingerprint}",
                    classes="section-help",
                )
                yield Checkbox(
                    "Replace stored key", id="replace-key", value=False,
                )
                # The replacement input starts disabled; toggled by the
                # checkbox (see on_checkbox_changed).
                inp = Input(
                    placeholder="sk-ant-...",
                    password=True,
                    id="api-key-input",
                    disabled=True,
                )
                yield inp
            else:
                yield Label(
                    "  No key stored. Paste one from console.anthropic.com:",
                    classes="section-help",
                )
                yield Input(
                    placeholder="sk-ant-...",
                    password=True,
                    id="api-key-input",
                )

            yield Label("Toggles", classes="section-header")
            yield Checkbox(
                "Enable cloud LLM",
                id="toggle-enabled",
                value=s.enabled,
            )
            yield Checkbox(
                "Use for /research synth (recommended)",
                id="toggle-synth",
                value=s.research_synth,
            )
            yield Checkbox(
                "Use for /research planner (opt-in)",
                id="toggle-plan",
                value=s.research_plan,
            )

            yield Label("Model", classes="section-header")
            yield Label(
                "  Haiku: ~$0.024/run, fast. "
                "Sonnet: 3x more, better on complex synthesis. "
                "Opus: 5x more, max quality.",
                classes="section-help",
            )
            with RadioSet(id="model-set"):
                for model_id in ALLOWED_MODELS:
                    yield RadioButton(model_id, value=(model_id == s.model))

            with Horizontal(id="cloud-buttons"):
                yield Button("Cancel", id="cancel-btn", variant="default")
                yield Button("Save", id="save-btn", variant="primary")

    def on_mount(self) -> None:
        # Land focus on the key input if there's no key yet (most common
        # first-use path), otherwise on Save.
        if self._state.key_fingerprint is None:
            try:
                self.query_one("#api-key-input", Input).focus()
                return
            except Exception:
                pass
        self.query_one("#save-btn", Button).focus()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        # "Replace stored key" toggles the enabled state of the key input.
        if event.checkbox.id == "replace-key":
            try:
                inp = self.query_one("#api-key-input", Input)
            except Exception:
                return
            inp.disabled = not event.value
            if event.value:
                inp.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            self.dismiss(self._collect())
        elif event.button.id == "cancel-btn":
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _collect(self) -> CloudModalResult:
        enabled = self.query_one("#toggle-enabled", Checkbox).value
        synth = self.query_one("#toggle-synth", Checkbox).value
        plan = self.query_one("#toggle-plan", Checkbox).value

        # Selected model from the radio group.
        radio = self.query_one("#model-set", RadioSet)
        model = self._state.model  # fallback to current
        pressed = getattr(radio, "pressed_button", None)
        if pressed is not None:
            model = str(pressed.label)

        # API key: only pass up if user actually typed something AND
        # either (a) no key was stored, or (b) they checked "Replace".
        new_key: str | None = None
        try:
            inp = self.query_one("#api-key-input", Input)
        except Exception:
            inp = None
        if inp is not None and not inp.disabled:
            raw = inp.value.strip()
            if raw:
                new_key = raw

        return CloudModalResult(
            enabled=enabled,
            research_synth=synth,
            research_plan=plan,
            model=model,
            new_api_key=new_key,
        )
