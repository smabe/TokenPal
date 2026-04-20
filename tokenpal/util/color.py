"""Hex-color utilities for terminal / Rich-markup palettes."""

from __future__ import annotations


def hex_to_hue_bucket(hex_str: str) -> str:
    """Map a ``#rrggbb`` hex string to a coarse hue-family label.

    Returns one of ``white``, ``black``, ``gray``, ``red``, ``orange``,
    ``yellow``, ``green``, ``cyan``, ``blue``, ``purple``, ``pink``.
    Fuzzy on exact hex; strict on hue band so ``#00ffff`` and ``#66ffff``
    both resolve to ``cyan``. Used by voice-training golden assertions
    ("Finn's outfit must land in the cyan bucket") where the exact hex
    the LLM picks drifts across runs but the family it lands in
    shouldn't.
    """
    h = hex_str.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    m, M = min(r, g, b), max(r, g, b)
    if M - m < 25:
        if M > 220:
            return "white"
        if M < 40:
            return "black"
        return "gray"
    if M == r:
        hue = (60 * (g - b) / (M - m)) % 360
    elif M == g:
        hue = 60 * (b - r) / (M - m) + 120
    else:
        hue = 60 * (r - g) / (M - m) + 240
    if hue < 15 or hue >= 345:
        return "red"
    if hue < 45:
        return "orange"
    if hue < 70:
        return "yellow"
    if hue < 170:
        return "green"
    if hue < 200:
        return "cyan"
    if hue < 260:
        return "blue"
    if hue < 310:
        return "purple"
    return "pink"
