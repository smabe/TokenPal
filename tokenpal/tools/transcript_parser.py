"""Parse character dialogue from plain-text transcripts."""

from __future__ import annotations

import re
from pathlib import Path

# Format A: ALL-CAPS character name on its own line (screenplay style)
#   MICHAEL SCOTT
#   That's what she said.
_STANDALONE_NAME_RE = re.compile(r"^([A-Z][A-Z .'\-]{0,30})$")

# Format B: "Name: dialogue" on the same line (wiki/fan transcript style)
#   Mordecai: Dude, check this out.
_INLINE_NAME_RE = re.compile(r"^([A-Za-z][A-Za-z .'\-]{0,30}):\s+(.+)")

# Stage directions / HTML tags to strip
_STAGE_DIR_RE = re.compile(r"\[[^\]]*\]|\([^)]*\)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _clean_line(line: str) -> str:
    """Strip stage directions, HTML tags, and excess whitespace."""
    line = _HTML_TAG_RE.sub("", line)
    line = _STAGE_DIR_RE.sub("", line)
    return line.strip().strip("\"'").strip()


def _detect_format(text: str) -> str:
    """Detect transcript format: 'standalone', 'inline', or 'lines'."""
    standalone = 0
    inline = 0
    count = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        count += 1
        if _STANDALONE_NAME_RE.match(line):
            standalone += 1
        if _INLINE_NAME_RE.match(line):
            inline += 1
        if count >= 100:
            break

    if count == 0:
        return "lines"
    # Whichever pattern has more hits wins
    if inline / count >= 0.10:
        return "inline"
    if standalone / count >= 0.10:
        return "standalone"
    return "lines"


def _parse_standalone(text: str, character: str) -> list[str]:
    """Extract dialogue from screenplay format (ALL-CAPS name on own line)."""
    character_upper = character.upper()
    lines: list[str] = []
    capturing = False

    for raw_line in text.splitlines():
        stripped = raw_line.strip()

        if _STANDALONE_NAME_RE.match(stripped):
            capturing = character_upper in stripped.upper()
            continue

        if not stripped:
            capturing = False
            continue

        if capturing:
            cleaned = _clean_line(stripped)
            if cleaned:
                lines.append(cleaned)

    return lines


def _parse_inline(text: str, character: str) -> list[str]:
    """Extract dialogue from 'Name: dialogue' format (wiki transcripts)."""
    character_lower = character.lower()
    lines: list[str] = []

    for raw_line in text.splitlines():
        m = _INLINE_NAME_RE.match(raw_line.strip())
        if m and character_lower in m.group(1).lower():
            cleaned = _clean_line(m.group(2))
            if cleaned:
                lines.append(cleaned)

    return lines


def _parse_lines_file(text: str) -> list[str]:
    """Parse a simple one-quote-per-line file."""
    lines: list[str] = []
    for raw_line in text.splitlines():
        cleaned = _clean_line(raw_line.strip())
        if cleaned:
            lines.append(cleaned)
    return lines


def _filter_lines(
    lines: list[str], min_len: int = 8, max_len: int = 150
) -> list[str]:
    """Apply length filtering and deduplication."""
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        if len(line) < min_len or len(line) > max_len:
            continue
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(line)
    return result


def extract_lines(
    path: Path,
    character: str | None = None,
    lines_only: bool = False,
    min_len: int = 8,
    max_len: int = 150,
) -> list[str]:
    """Extract and filter character lines from a transcript file.

    Supports three formats (auto-detected):
    - Standalone: ALL-CAPS name on own line, dialogue below
    - Inline: "Name: dialogue" on the same line (wiki/fan transcripts)
    - Lines: one quote per line (no character names)
    """
    text = path.read_text(encoding="utf-8", errors="replace")

    if lines_only:
        raw = _parse_lines_file(text)
    elif character:
        fmt = _detect_format(text)
        if fmt == "inline":
            raw = _parse_inline(text, character)
        elif fmt == "standalone":
            raw = _parse_standalone(text, character)
        else:
            # Try both, take whichever finds lines
            raw = _parse_inline(text, character)
            if not raw:
                raw = _parse_standalone(text, character)
            if not raw:
                raw = _parse_lines_file(text)
    else:
        raw = _parse_lines_file(text)

    return _filter_lines(raw, min_len=min_len, max_len=max_len)
