"""Modal for /cloud - manage all three cloud backends in one screen.

Exposes:
  - Anthropic section: API key input (with "replace stored key" toggle
    when a key is already saved), enable/synth/plan/search/deep
    checkboxes, model radio (haiku / sonnet / opus).
  - Tavily section: API key input with the same replace-stored pattern,
    an "enable as default /research search backend" checkbox, and a
    basic/advanced depth radio.
  - Brave section: API key input only — Brave has no runtime flag, so
    presence of the key = active.

Pure UI: the modal dismisses with a ``CloudModalResult`` dataclass;
the caller (tokenpal/app.py) persists the changes via secrets.py,
cloud_writer.py, and toml_writer.py.
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
    key_fingerprint: str | None  # None when no Anthropic key stored
    # Tavily (cloud search layer)
    tavily_enabled: bool = False
    tavily_search_depth: str = "advanced"  # "basic" | "advanced"
    tavily_key_fingerprint: str | None = None
    # Brave (key-only; no runtime flag — presence of key = active)
    brave_key_fingerprint: str | None = None


@dataclass(frozen=True)
class CloudModalResult:
    """Result from the modal. ``new_api_key`` is None when the user did
    not enter (or asked to replace) a key; the caller keeps whatever is
    on disk already. Same applies to ``tavily_new_api_key`` and
    ``brave_new_api_key``."""

    enabled: bool
    research_synth: bool
    research_plan: bool
    research_deep: bool
    research_search: bool
    model: str
    new_api_key: str | None
    # Tavily
    tavily_enabled: bool = False
    tavily_search_depth: str = "advanced"
    tavily_new_api_key: str | None = None
    # Brave
    brave_new_api_key: str | None = None


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

            # ------------------------------------------------------------
            # Tavily section
            # ------------------------------------------------------------
            yield Label("Tavily (search + extract)", classes="section-header")
            yield Label(
                "  LLM-optimized search with preloaded article content; "
                "when on, /research uses Tavily as the default search "
                "backend.",
                classes="section-help",
            )
            if s.tavily_key_fingerprint is not None:
                yield Label(
                    f"  Key stored: {s.tavily_key_fingerprint}",
                    classes="section-help",
                )
                yield Checkbox(
                    "Replace stored key",
                    id="tavily-replace-key",
                    value=False,
                )
                yield Input(
                    placeholder="tvly-...",
                    password=True,
                    id="tavily-api-key-input",
                    disabled=True,
                )
            else:
                yield Label(
                    "  No key stored. Paste one from app.tavily.com:",
                    classes="section-help",
                )
                yield Input(
                    placeholder="tvly-...",
                    password=True,
                    id="tavily-api-key-input",
                )
            yield Checkbox(
                "Enable Tavily as default search",
                id="toggle-tavily-enabled",
                value=s.tavily_enabled,
            )
            with RadioSet(id="tavily-depth-set"):
                for depth in ("basic", "advanced"):
                    yield RadioButton(
                        depth, value=(depth == s.tavily_search_depth),
                    )

            # ------------------------------------------------------------
            # Brave section
            # ------------------------------------------------------------
            yield Label(
                "Brave (alternative web search)", classes="section-header",
            )
            yield Label(
                "  Free-tier web search (2k queries/month); the planner "
                "routes queries here when it picks the brave backend.",
                classes="section-help",
            )
            if s.brave_key_fingerprint is not None:
                yield Label(
                    f"  Key stored: {s.brave_key_fingerprint}",
                    classes="section-help",
                )
                yield Checkbox(
                    "Replace stored key",
                    id="brave-replace-key",
                    value=False,
                )
                yield Input(
                    placeholder="BSA-...",
                    password=True,
                    id="brave-api-key-input",
                    disabled=True,
                )
            else:
                yield Label(
                    "  No key stored. Paste one from "
                    "api.search.brave.com:",
                    classes="section-help",
                )
                yield Input(
                    placeholder="BSA-...",
                    password=True,
                    id="brave-api-key-input",
                )

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
        # Each backend has its own replace-key checkbox + input pair.
        replace_map = {
            "replace-key": "#api-key-input",
            "tavily-replace-key": "#tavily-api-key-input",
            "brave-replace-key": "#brave-api-key-input",
        }
        target = replace_map.get(event.checkbox.id or "")
        if target is None:
            return
        try:
            inp = self.query_one(target, Input)
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
        new_key = self._collect_key_input("#api-key-input")
        tavily_new_key = self._collect_key_input("#tavily-api-key-input")
        brave_new_key = self._collect_key_input("#brave-api-key-input")

        # Tavily toggles
        try:
            tavily_enabled = bool(
                self.query_one("#toggle-tavily-enabled", Checkbox).value
            )
        except Exception:
            tavily_enabled = self._state.tavily_enabled
        tavily_depth = self._selected_tavily_depth()

        return CloudModalResult(
            enabled=enabled,
            research_synth=synth,
            research_plan=plan,
            research_deep=deep,
            research_search=search,
            model=model,
            new_api_key=new_key,
            tavily_enabled=tavily_enabled,
            tavily_search_depth=tavily_depth,
            tavily_new_api_key=tavily_new_key,
            brave_new_api_key=brave_new_key,
        )

    def _collect_key_input(self, selector: str) -> str | None:
        try:
            inp = self.query_one(selector, Input)
        except Exception:
            return None
        if inp.disabled:
            return None
        raw = inp.value.strip()
        return raw or None

    def _selected_tavily_depth(self) -> str:
        try:
            radio = self.query_one("#tavily-depth-set", RadioSet)
        except Exception:
            return self._state.tavily_search_depth
        pressed = getattr(radio, "pressed_button", None)
        if pressed is None:
            return self._state.tavily_search_depth
        label = str(pressed.label).strip()
        return label if label in ("basic", "advanced") else (
            self._state.tavily_search_depth
        )
