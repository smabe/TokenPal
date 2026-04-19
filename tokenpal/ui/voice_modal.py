"""Modal for /voice — discoverable UI for every /voice subcommand.

Pure UI: the modal dismisses with a ``VoiceModalResult`` dataclass
encoding the chosen action + payload; the caller (tokenpal/app.py)
dispatches to the matching ``_voice_*`` / ``_start_voice_*`` helper so
the slash command and the modal share one implementation.

Layout (top-down):
  1. Status        — active voice info (mirrors /voice info), plus
                     "Use default voice" button when a custom voice
                     is active (maps to /voice off).
  2. Saved voices  — OptionList; Switch button maps to /voice switch.
  3. Train new     — wiki URL + character inputs; Train button maps
                     to /voice train.
  4. Finetune      — Run / Setup buttons; disabled with help text when
                     no custom voice is active.
  5. Regenerate    — All / ASCII buttons; disabled when no custom
                     voice is active. "All" is confirm-gated upstream
                     (app layer) because it's a ~60s LLM job.
  6. Import        — path input + Import button.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Literal

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList
from textual.widgets.option_list import Option

from tokenpal.tools.voice_profile import ProfileSummary

VoiceAction = Literal[
    "switch",
    "off",
    "train",
    "finetune",
    "finetune_setup",
    "regenerate",
    "ascii",
    "import",
]


@dataclass(frozen=True)
class VoiceModalState:
    """Current state fed into the modal.

    ``active_voice`` is None when the default TokenPal voice is in use.
    """

    active_voice: ProfileSummary | None
    saved: list[ProfileSummary] = field(default_factory=list)


@dataclass(frozen=True)
class VoiceModalResult:
    """Result from the modal. ``action`` is None on Cancel/Esc (the
    screen resolves to ``None`` directly in that case; this dataclass
    is only constructed when a real action was chosen)."""

    action: VoiceAction
    payload: dict[str, str] = field(default_factory=dict)


class VoiceModal(ModalScreen["VoiceModalResult | None"]):
    """Voice management modal. Resolves to VoiceModalResult on action,
    None on Cancel / Esc."""

    DEFAULT_CSS: ClassVar[str] = """
    VoiceModal {
        align: center middle;
    }
    VoiceModal #voice-body {
        width: 70%;
        min-width: 50;
        max-width: 140;
        height: auto;
        max-height: 90%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    VoiceModal Label {
        width: 100%;
        height: auto;
    }
    VoiceModal #voice-title {
        text-style: bold;
        padding-bottom: 1;
    }
    VoiceModal .section-header {
        text-style: bold;
        color: $accent;
        padding-top: 1;
    }
    VoiceModal .section-help {
        color: $text-muted;
        padding-bottom: 1;
        padding-left: 2;
    }
    VoiceModal .section-disabled {
        color: $text-muted;
        text-style: italic;
        padding-bottom: 1;
        padding-left: 2;
    }
    VoiceModal Input {
        margin-bottom: 1;
        width: 100%;
    }
    VoiceModal #saved-list {
        height: auto;
        max-height: 12;
        margin-bottom: 1;
    }
    VoiceModal .action-row {
        height: auto;
        width: 100%;
        padding-top: 1;
    }
    VoiceModal #voice-buttons {
        height: auto;
        width: 100%;
        padding-top: 1;
        align-horizontal: right;
    }
    VoiceModal Button {
        margin-left: 1;
    }
    """

    BINDINGS: ClassVar[
        list[Binding | tuple[str, str] | tuple[str, str, str]]
    ] = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(self, state: VoiceModalState) -> None:
        super().__init__()
        self._state = state

    def compose(self) -> ComposeResult:
        s = self._state
        has_active = s.active_voice is not None

        with VerticalScroll(id="voice-body"):
            yield Label("Voice", id="voice-title")

            # ------------------------------------------------------------
            # Status
            # ------------------------------------------------------------
            yield Label("Status", classes="section-header")
            yield Label(_format_status(s), classes="section-help")
            if has_active:
                with Horizontal(classes="action-row"):
                    yield Button(
                        "Use default voice",
                        id="off-btn",
                        variant="warning",
                    )

            # ------------------------------------------------------------
            # Saved voices
            # ------------------------------------------------------------
            yield Label("Saved voices", classes="section-header")
            if s.saved:
                yield Label(
                    "Pick one and press Switch to activate.",
                    classes="section-help",
                )
                yield OptionList(
                    *[
                        Option(_format_saved_row(v), id=v.slug)
                        for v in s.saved
                    ],
                    id="saved-list",
                )
                with Horizontal(classes="action-row"):
                    yield Button(
                        "Switch", id="switch-btn", variant="primary",
                    )
            else:
                yield Label(
                    "No voices saved yet. Train one below.",
                    classes="section-disabled",
                )

            # ------------------------------------------------------------
            # Train new
            # ------------------------------------------------------------
            yield Label("Train new voice", classes="section-header")
            yield Label(
                "Paste a Fandom wiki URL and the character name.",
                classes="section-help",
            )
            yield Input(
                placeholder="https://adventuretime.fandom.com/wiki/Finn",
                id="train-wiki-input",
            )
            yield Input(
                placeholder="Finn the Human",
                id="train-character-input",
            )
            with Horizontal(classes="action-row"):
                yield Button("Train", id="train-btn", variant="primary")

            # ------------------------------------------------------------
            # Finetune active voice
            # ------------------------------------------------------------
            yield Label("Fine-tune", classes="section-header")
            if has_active:
                yield Label(
                    "Run LoRA fine-tuning on the remote GPU for the "
                    "active voice, or set up the remote host first.",
                    classes="section-help",
                )
                with Horizontal(classes="action-row"):
                    yield Button("Run fine-tune", id="finetune-btn")
                    yield Button(
                        "Setup remote host...",
                        id="finetune-setup-btn",
                    )
            else:
                yield Label(
                    "Switch to a custom voice first — fine-tuning "
                    "targets the active voice.",
                    classes="section-disabled",
                )

            # ------------------------------------------------------------
            # Regenerate
            # ------------------------------------------------------------
            yield Label("Regenerate assets", classes="section-header")
            if has_active:
                yield Label(
                    "Refresh LLM-backed persona + ASCII art. 'All' takes "
                    "about a minute.",
                    classes="section-help",
                )
                with Horizontal(classes="action-row"):
                    yield Button(
                        "Regenerate all assets",
                        id="regenerate-btn",
                        variant="warning",
                    )
                    yield Button(
                        "Regenerate ASCII art only", id="ascii-btn",
                    )
            else:
                yield Label(
                    "Switch to a custom voice first — regenerate "
                    "operates on the active voice.",
                    classes="section-disabled",
                )

            # ------------------------------------------------------------
            # Import
            # ------------------------------------------------------------
            yield Label("Import GGUF", classes="section-header")
            yield Label(
                "Path to a .gguf file trained elsewhere (the stem must "
                "match an existing voice slug).",
                classes="section-help",
            )
            yield Input(
                placeholder="~/finetunes/mordecai.gguf",
                id="import-path-input",
            )
            with Horizontal(classes="action-row"):
                yield Button("Import", id="import-btn")

            # ------------------------------------------------------------
            # Cancel
            # ------------------------------------------------------------
            with Horizontal(id="voice-buttons"):
                yield Button("Close", id="cancel-btn", variant="default")

    def on_mount(self) -> None:
        try:
            self.query_one("#voice-body", VerticalScroll).scroll_home(
                animate=False,
            )
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id == "cancel-btn":
            self.dismiss(None)
        elif btn_id == "off-btn":
            self.dismiss(VoiceModalResult(action="off"))
        elif btn_id == "switch-btn":
            slug = self._selected_saved_slug()
            if slug:
                self.dismiss(
                    VoiceModalResult(action="switch", payload={"name": slug})
                )
        elif btn_id == "train-btn":
            wiki = self._input_value("#train-wiki-input")
            character = self._input_value("#train-character-input")
            if wiki and character:
                self.dismiss(
                    VoiceModalResult(
                        action="train",
                        payload={"wiki": wiki, "character": character},
                    )
                )
        elif btn_id == "finetune-btn":
            active = self._state.active_voice
            if active:
                self.dismiss(
                    VoiceModalResult(
                        action="finetune", payload={"name": active.slug},
                    )
                )
        elif btn_id == "finetune-setup-btn":
            self.dismiss(VoiceModalResult(action="finetune_setup"))
        elif btn_id == "regenerate-btn":
            self.dismiss(VoiceModalResult(action="regenerate"))
        elif btn_id == "ascii-btn":
            self.dismiss(VoiceModalResult(action="ascii"))
        elif btn_id == "import-btn":
            path = self._input_value("#import-path-input")
            if path:
                self.dismiss(
                    VoiceModalResult(
                        action="import", payload={"path": path},
                    )
                )

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _input_value(self, selector: str) -> str:
        try:
            return self.query_one(selector, Input).value.strip()
        except Exception:
            return ""

    def _selected_saved_slug(self) -> str:
        try:
            option_list = self.query_one("#saved-list", OptionList)
        except Exception:
            return ""
        idx = option_list.highlighted
        if idx is None:
            return ""
        try:
            return option_list.get_option_at_index(idx).id or ""
        except Exception:
            return ""


def _format_status(state: VoiceModalState) -> str:
    active = state.active_voice
    if active is None:
        return "Default TokenPal voice."
    ft = " (fine-tuned)" if active.finetuned_model else ""
    parts = [f"Voice: {active.character}{ft}"]
    if active.source:
        parts.append(f"source: {active.source}")
    parts.append(f"{active.line_count} lines")
    if active.finetuned_model:
        parts.append(f"model: {active.finetuned_model}")
    return " | ".join(parts)


def _format_saved_row(summary: ProfileSummary) -> str:
    ft = " [FT]" if summary.finetuned_model else ""
    return f"{summary.character} ({summary.line_count} lines){ft}"
