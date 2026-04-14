"""Pure-logic ASCII buddy rendering — no platform dependencies."""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass

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

_TAG_RE = re.compile(r"\[([^\[\]/][^\[\]]*)\]")


def _remap_rich_only_names(line: str) -> str:
    def repl(match: re.Match[str]) -> str:
        inner = match.group(1)
        tokens = [RICH_ONLY_COLOR_HEX.get(tok.lower(), tok) for tok in inner.split()]
        return "[" + " ".join(tokens) + "]"

    return _TAG_RE.sub(repl, line)


def _fix_markup(lines: list[str]) -> list[str]:
    """Fix LLM-generated Rich markup for Textual compatibility."""
    out = [_NAMED_COLOR_RE.sub(r"[\1]", line) for line in lines]
    return [_remap_rich_only_names(line) for line in out]


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

    def render(self) -> list[str]:
        """Render text inside an ASCII speech bubble."""
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
