"""Pure-logic ASCII buddy rendering — no platform dependencies."""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field

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

    @staticmethod
    def get(name: str) -> BuddyFrame:
        return BuddyFrame(lines=FRAMES.get(name, BUDDY_IDLE), name=name)


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
