"""Shared text-quality guards for LLM output.

A line is "clean English" when it's mostly ASCII printable and contains no
known chain-of-thought / meta-commentary markers. Used by both voice
training and the runtime response filter.
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


def is_clean_english(
    text: str, *, max_nonascii_ratio: float = 0.10,
) -> bool:
    """Reject drift: non-English, chain-of-thought, markdown meta-commentary.

    A line passes if it's mostly ASCII printable and contains no known
    meta-commentary tokens. Empty strings fail. Small amounts of accented
    characters (<= 10% by default) are allowed so legitimate words like
    ``café`` aren't flagged.
    """
    if not text or not text.strip():
        return False
    stripped = text.strip()
    if stripped.startswith("**") and stripped.endswith("**"):
        return False
    if stripped.endswith(":**") or stripped.endswith("**:"):
        return False
    nonascii = sum(1 for ch in stripped if ord(ch) > 127)
    if nonascii / len(stripped) > max_nonascii_ratio:
        return False
    lower = stripped.lower()
    return not any(marker in lower for marker in _META_MARKERS)
