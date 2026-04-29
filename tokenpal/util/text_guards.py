"""Shared text-quality guards for LLM output.

A line is "clean English" when every character is in Latin script (or common
punctuation) and contains no known chain-of-thought / meta-commentary
markers. Used by both voice training and the runtime response filter.
"""

from __future__ import annotations

_META_MARKERS = (
    "wikipedia",
    "copiert",
    "paste von",
    "analyze the",
    "user's request",
    "i cannot provide",
    "if the goal is",
    "the preceding text",
    "the user's prompt",
    "**analyze",
    "codiert",
    "nachweislich",
)


def truncate_ellipsis(text: str, max_chars: int) -> str:
    """Cap *text* at *max_chars* and append an ellipsis if truncated."""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _is_latin_or_punct(ch: str) -> bool:
    code = ord(ch)
    if code < 0x80:
        return True
    if 0x00A0 <= code <= 0x024F:
        return True
    if 0x1E00 <= code <= 0x1EFF:
        return True
    if 0x2000 <= code <= 0x206F:
        return True
    if 0x20A0 <= code <= 0x20CF:
        return True
    return False


def is_clean_english(text: str) -> bool:
    """Reject drift: non-Latin script, chain-of-thought, markdown meta-commentary.

    Every character must be ASCII, a Latin-script accented letter
    (``café``, ``naïve``), or common punctuation/currency. CJK ideographs,
    Cyrillic, Greek, Hebrew, Arabic, Thai, Devanagari, etc. fail outright —
    those are always LLM drift in an English-voice buddy, regardless of how
    few characters they make up.
    """
    if not text or not text.strip():
        return False
    stripped = text.strip()
    if stripped.startswith("**") and stripped.endswith("**"):
        return False
    if stripped.endswith(":**") or stripped.endswith("**:"):
        return False
    if not all(_is_latin_or_punct(ch) for ch in stripped):
        return False
    lower = stripped.lower()
    return not any(marker in lower for marker in _META_MARKERS)
