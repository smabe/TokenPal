"""Shared utilities for app awareness senses across platforms."""

from __future__ import annotations

# Safe title patterns that are OK to pass through (music players in browser)
SAFE_TITLE_SUFFIXES: tuple[str, ...] = (
    "- YouTube Music",
    "- Spotify Web Player",
    "- Apple Music",
)


def sanitize_browser_title(
    app_identifier: str, title: str, browsers: set[str]
) -> str:
    """Strip browser window titles unless they match a known safe pattern.

    ``browsers`` entries must be lowercase — the identifier is lower-cased
    before lookup.
    """
    if app_identifier.lower() not in browsers:
        return title
    if any(title.endswith(suffix) for suffix in SAFE_TITLE_SUFFIXES):
        return title
    return ""
