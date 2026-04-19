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

from tokenpal.llm.cloud_backend import ALLOWED_MODELS, DEEP_MODE_MODELS


@dataclass(frozen=True)
class CloudModalState:
    """Current state fed into the modal."""

    enabled: bool
    research_synth: bool
    research_plan: bool
    research_deep: bool
    research_search: bool
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
    research_deep: bool
    research_search: bool
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
            # Search mode (cheap): Sonnet + web_search only, no web_fetch.
            search_disabled = s.model not in DEEP_MODE_MODELS
            yield Checkbox(
                "Use cloud web search (Sonnet+ only)",
                id="toggle-search",
                value=s.research_search and not search_disabled,
                disabled=search_disabled,
            )
            # Deep mode (expensive): server-side web_search + web_fetch.
            deep_disabled = s.model not in DEEP_MODE_MODELS
            yield Checkbox(
                "Use deep web search + fetch (Sonnet+, WARNING $1-3/run)",
                id="toggle-deep",
                value=s.research_deep and not deep_disabled,
                disabled=deep_disabled,
            )
            yield Label(
                "",
                id="deep-help",
                classes="section-help",
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
        self._refresh_deep_help()
        # Land focus on the key input if there's no key yet (most common
        # first-use path), otherwise on Save.
        if self._state.key_fingerprint is None:
            try:
                self.query_one("#api-key-input", Input).focus()
                return
            except Exception:
                pass
        self.query_one("#save-btn", Button).focus()

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        # Re-gate the search + deep checkboxes when the user picks a
        # different model. Haiku forces both off.
        if event.radio_set.id != "model-set":
            return
        model = self._selected_model()
        eligible = model in DEEP_MODE_MODELS
        for cb_id in ("#toggle-search", "#toggle-deep"):
            try:
                cb = self.query_one(cb_id, Checkbox)
            except Exception:
                continue
            cb.disabled = not eligible
            if not eligible:
                cb.value = False
        self._refresh_deep_help()

    def _selected_model(self) -> str:
        try:
            radio = self.query_one("#model-set", RadioSet)
        except Exception:
            return self._state.model
        pressed = getattr(radio, "pressed_button", None)
        return str(pressed.label) if pressed is not None else self._state.model

    def _refresh_deep_help(self) -> None:
        try:
            label = self.query_one("#deep-help", Label)
        except Exception:
            return
        model = self._selected_model()
        if model not in DEEP_MODE_MODELS:
            label.update(
                "  Haiku doesn't support dynamic-filtering web tools. "
                "Pick Sonnet 4.6 or Opus 4.7."
            )
        else:
            label.update(
                "  Search: cheap, snippets only. Deep: expensive "
                "($1-3/run), fetches full pages. If both set, deep wins."
            )

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
        try:
            deep_cb = self.query_one("#toggle-deep", Checkbox)
            deep = bool(deep_cb.value and not deep_cb.disabled)
        except Exception:
            deep = False
        try:
            search_cb = self.query_one("#toggle-search", Checkbox)
            search = bool(search_cb.value and not search_cb.disabled)
        except Exception:
            search = False

        # Selected model from the radio group.
        model = self._selected_model()

        # If user switched to Haiku but a cloud-web flag was left on,
        # force both off — the checkbox gating already does this via
        # on_radio_set_changed, but belt-and-suspenders for headless tests.
        if model not in DEEP_MODE_MODELS:
            deep = False
            search = False

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
            research_deep=deep,
            research_search=search,
            model=model,
            new_api_key=new_key,
        )
