"""Shared text-quality guards for LLM output.

A line is "clean English" when every character is in Latin script (or common
punctuation) and contains no known chain-of-thought / meta-commentary
markers. Used by both voice training and the runtime response filter.
"""

from __future__ import annotations

import re

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


# Characters a model renders as nothing but that would otherwise let untrusted
# text hide a closing tag from the matcher: whitespace, Unicode format chars,
# soft hyphen, and the C0/C1 controls that are not whitespace.
_IGN = r"\s\u00ad\u200b-\u200f\u2060\ufeff\x00-\x08\x0e-\x1f\x7f-\x9f"
_IGNORABLE = rf"[{_IGN}]*"


def _envelope_tag_pattern(tag: str) -> str:
    """Match <tag>, </tag>, <tag/>, and <tag attr="v">, tolerating ignorable
    characters interleaved anywhere inside the name. The name must be followed
    by a separator or the bracket, so <transcripts> is not a <transcript>."""
    spaced_name = _IGNORABLE.join(re.escape(ch) for ch in tag)
    return rf"<({_IGNORABLE}/?{_IGNORABLE}{spaced_name}(?:[{_IGN}/][^<>]*)?)>"


def neutralize_envelope_tags(text: str, tag: str = "transcript") -> str:
    """Rewrite any <tag> / </tag> in *text* with full-width angle brackets so
    it cannot close or open a prompt envelope.
    """
    pattern = _envelope_tag_pattern(tag)
    return re.sub(pattern, lambda m: f"＜{m.group(1)}＞", text, flags=re.IGNORECASE)


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


def is_latin_script(text: str) -> bool:
    """True if *text* is empty or every character is Latin script / punctuation.

    Used to drop external strings (GitHub/HN/Lobsters titles, descriptions)
    that contain CJK / Cyrillic / etc. before they reach the LLM prompt —
    the buddy speaks English, so non-Latin content is just noise to riff on
    and a drift trigger for the underlying model.
    """
    return all(_is_latin_or_punct(ch) for ch in text)


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
