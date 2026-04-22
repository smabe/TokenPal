"""Parse Rich-style ``[#hex]…[/]`` markup into colored segments for Qt.

Voice-specific buddy art is stored as Rich-markup lines (the Textual
overlay's native format). The Qt painter needs to draw each colored
span in the right ``QPen``, so we split the line into ``(text, color)``
segments and render them one at a time.

Named colors like ``[red]`` or ``[silver]`` are normalized to hex by
the existing ``ascii_renderer._fix_markup`` helper at frame-load time
(``QtOverlay.load_voice_frames`` can call it before it hands the frame
to the buddy window). This module handles the ``[#hex]`` and ``[/]``
subset that survives that normalization, plus a permissive fallback
that strips any tag it doesn't understand so unknown markup doesn't
leak into the visible output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_TAG_RE = re.compile(r"\[(/|#[0-9a-fA-F]{6})\]")
_ANY_TAG_RE = re.compile(r"\[[^\[\]]*\]")


@dataclass(frozen=True)
class Segment:
    """One run of same-colored characters. ``color`` is a hex string
    (``#rrggbb``) or ``None`` for the overlay's default foreground."""

    text: str
    color: str | None


def parse_markup(line: str) -> list[Segment]:
    """Split ``line`` into colored segments.

    Nested tags use a stack so closing ``[/]`` pops the innermost color.
    Unknown bracket content (e.g. ``[foo]``) is treated as literal text
    — we never swallow characters we can't recognize, so a misrendered
    line still shows its content rather than disappearing.
    """
    segments: list[Segment] = []
    stack: list[str | None] = [None]
    pos = 0
    for m in _TAG_RE.finditer(line):
        if m.start() > pos:
            segments.append(Segment(line[pos:m.start()], stack[-1]))
        tag = m.group(1)
        if tag == "/":
            if len(stack) > 1:
                stack.pop()
        else:
            stack.append(tag)
        pos = m.end()
    if pos < len(line):
        segments.append(Segment(line[pos:], stack[-1]))
    # Collapse empty segments — e.g. back-to-back tags produce ""s.
    return [s for s in segments if s.text]


def stripped_text(line: str) -> str:
    """Return ``line`` with every bracket tag removed. Used for layout
    measurements (font metrics don't know the tags are invisible)."""
    return _ANY_TAG_RE.sub("", line)
