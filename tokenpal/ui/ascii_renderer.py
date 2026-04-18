"""Pure-logic ASCII buddy rendering — no platform dependencies."""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass

from rich.errors import MarkupError
from rich.text import Text

BUDDY_IDLE = [
    r"     ▄███████████▄     ",
    r"   ▄█             █▄   ",
    r"   █   Ө       Ө   █   ",
    r"   █       ▽       █   ",
    r"    ▀██▄▄▄▄▄▄▄██▀     ",
    r"      █   ◇◇   █       ",
    r"      █▄▄▄▄▄▄▄█       ",
    r"      ▄▀▀▄ ▄▀▀▄       ",
]

BUDDY_TALKING = [
    r"     ▄███████████▄     ",
    r"   ▄█             █▄   ",
    r"   █   Ө       Ө   █   ",
    r"   █       ◇       █   ",
    r"    ▀██▄▄▄▄▄▄▄██▀     ",
    r"      █   ◇◇   █       ",
    r"      █▄▄▄▄▄▄▄█       ",
    r"      ▄▀▀▄ ▄▀▀▄       ",
]

BUDDY_THINKING = [
    r"     ▄███████████▄     ",
    r"   ▄█             █▄   ",
    r"   █   ─       ─   █   ",
    r"   █       ~       █   ",
    r"    ▀██▄▄▄▄▄▄▄██▀     ",
    r"      █   ◇◇   █       ",
    r"      █▄▄▄▄▄▄▄█       ",
    r"      ▄▀▀▄ ▄▀▀▄       ",
]

BUDDY_SURPRISED = [
    r"     ▄███████████▄     ",
    r"   ▄█             █▄   ",
    r"   █   Ф       Ф   █   ",
    r"   █       □       █   ",
    r"    ▀██▄▄▄▄▄▄▄██▀     ",
    r"      █   ◇◇   █       ",
    r"      █▄▄▄▄▄▄▄█       ",
    r"      ▄▀▀▄ ▄▀▀▄       ",
]

# Textual uses [color] for named colors but [#hex] for hex codes.
# LLM-generated art sometimes uses [#namedcolor] which Textual rejects.
_NAMED_COLOR_RE = re.compile(r"\[#(?![0-9a-fA-F]{6}\])([a-zA-Z]\w*)\]")

# Qwen3 sometimes emits junk-prefixed hex like [#$ff6600] or [#@ff6600].
# Strip any non-hex chars between `#` and the 6-hex-digit payload so the tag
# still renders. Applied before _NAMED_COLOR_RE so we don't misclassify.
_DIRTY_HEX_RE = re.compile(r"\[#[^0-9a-fA-F\]]+([0-9a-fA-F]{6})\]")

# LLMs occasionally leak HTML-style tags (<u>, </u>) into ASCII frames; Textual
# uses [bracket] markup so angle-bracket tags render as raw characters. Letter-
# start only so legit ASCII like "<- arrow ->" survives.
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^<>]*>")

# Rich accepts these CSS color names but Textual's Style.from_rich_style
# rejects them with MissingStyle. Remap to hex so both renderers work.
RICH_ONLY_COLOR_HEX: dict[str, str] = {
    "silver": "#c0c0c0",
    "gray": "#808080",
    "grey": "#808080",
    "darkgray": "#505050",
    "darkgrey": "#505050",
    "lightgray": "#d3d3d3",
    "lightgrey": "#d3d3d3",
    "gold": "#ffd700",
    "orange": "#ffa500",
    "pink": "#ffc0cb",
    "purple": "#800080",
    "brown": "#a52a2a",
    "navy": "#000080",
    "teal": "#008080",
    "olive": "#808000",
    "maroon": "#800000",
    "lime": "#00ff00",
    "aqua": "#00ffff",
    "fuchsia": "#ff00ff",
}

_TAG_RE = re.compile(r"\[(/?)([a-zA-Z][^\[\]]*)\]")


def _remap_rich_only_names(line: str) -> str:
    def repl(match: re.Match[str]) -> str:
        slash, inner = match.group(1), match.group(2)
        tokens = [RICH_ONLY_COLOR_HEX.get(tok.lower(), tok) for tok in inner.split()]
        return "[" + slash + " ".join(tokens) + "]"

    return _TAG_RE.sub(repl, line)


# Permissive: matches any `[...]` that could plausibly be a Rich tag, including
# bare closers like `[/]`. Only used as a last resort when `_TAG_RE` can't see
# it as a proper opener but the content is still choking the markup parser.
_ANY_BRACKET_TAG_RE = re.compile(r"\[/?[^\[\]]*\]")


def _strip_markup(line: str) -> str:
    """Last-resort: remove every bracket tag from a line so raw glyphs remain."""
    return _ANY_BRACKET_TAG_RE.sub("", line)


def _fix_markup(lines: list[str]) -> list[str]:
    """Fix LLM-generated Rich markup for Textual compatibility.

    Runs four passes:
    1. Heal junk-prefixed hex tokens (``[#$ff6600]`` → ``[#ff6600]``).
    2. Drop ``#`` from tokens with a valid named color body (``[#red]`` → ``[red]``).
    3. Remap Rich-only names (``silver`` → ``#c0c0c0``) so Textual accepts them.
    4. Strip any stray HTML-style tags.

    After all repairs, verify each line parses with ``Text.from_markup``. If it
    still raises (a [/] tag with nothing to close, etc.), strip every bracket
    tag from that line as a last-resort fallback so the renderer can't crash.
    """
    out = [_DIRTY_HEX_RE.sub(r"[#\1]", line) for line in lines]
    out = [_NAMED_COLOR_RE.sub(r"[\1]", line) for line in out]
    out = [_remap_rich_only_names(line) for line in out]
    out = [_HTML_TAG_RE.sub("", line) for line in out]

    safe: list[str] = []
    for line in out:
        try:
            Text.from_markup(line)
        except MarkupError:
            safe.append(_strip_markup(line))
        else:
            safe.append(line)
    return safe


FRAMES: dict[str, list[str]] = {
    "idle": BUDDY_IDLE,
    "talking": BUDDY_TALKING,
    "thinking": BUDDY_THINKING,
    "surprised": BUDDY_SURPRISED,
}


@dataclass
class BuddyFrame:
    lines: list[str]
    name: str = "idle"
    markup: bool = False

    @staticmethod
    def get(name: str) -> BuddyFrame:
        return BuddyFrame(lines=FRAMES.get(name, BUDDY_IDLE), name=name)

    @staticmethod
    def from_voice(
        name: str,
        idle: list[str],
        idle_alt: list[str],
        talking: list[str],
    ) -> dict[str, BuddyFrame]:
        """Build a frame set from voice profile art. Returns name→frame dict."""
        frames: dict[str, BuddyFrame] = {}
        if idle:
            frames["idle"] = BuddyFrame(
                lines=_fix_markup(idle), name="idle", markup=True,
            )
        if idle_alt:
            frames["idle_alt"] = BuddyFrame(
                lines=_fix_markup(idle_alt), name="idle_alt", markup=True,
            )
        if talking:
            frames["talking"] = BuddyFrame(
                lines=_fix_markup(talking), name="talking", markup=True,
            )
        return frames


@dataclass
class SpeechBubble:
    text: str
    style: str = "speech"
    max_width: int = 40
    persistent: bool = False
    borderless: bool = False

    def render(self) -> list[str]:
        """Render text inside an ASCII speech bubble."""
        if self.borderless:
            wrapped = textwrap.wrap(self.text, width=max(1, self.max_width))
            return list(wrapped)

        wrapped = textwrap.wrap(self.text, width=self.max_width - 4)
        if not wrapped:
            return []

        inner_width = max(len(line) for line in wrapped)
        border = "─" * (inner_width + 2)

        lines: list[str] = []
        lines.append(f"╭{border}╮")
        for line in wrapped:
            lines.append(f"│ {line:<{inner_width}} │")
        lines.append(f"╰{border}╯")

        # Speech tail (points down toward buddy)
        if self.style == "speech":
            lines.append("  ╲")
        elif self.style == "thought":
            lines.append("   ○")
            lines.append("    ○")
        elif self.style == "shout":
            lines.append("  ⚡")

        return lines


def render_buddy_with_bubble(
    frame: BuddyFrame, bubble: SpeechBubble | None = None
) -> str:
    """Combine bubble (above) and buddy (below) into a single text block."""
    parts: list[str] = []
    if bubble:
        parts.extend(bubble.render())
        parts.append("")  # blank line between bubble and buddy
    parts.extend(frame.lines)
    return "\n".join(parts)
