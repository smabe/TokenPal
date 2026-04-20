"""ASCII sprites + Style cache for the buddy environment overlay.

Hex-only colors — Rich-only color names crash Textual's stricter style parser
(see ascii_renderer._fix_markup for the same constraint elsewhere).
``Style.parse`` is the hot-path expense, not Segment construction; we build
every Style we need at import time and reuse them per-tick.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.style import Style

from tokenpal.ui.buddy_environment import EnvState, Kind

# Per-color cached Style objects. Particles look up by hex string.
_STYLE_CACHE: dict[str, Style] = {}


def style_for(color: str) -> Style:
    cached = _STYLE_CACHE.get(color)
    if cached is None:
        cached = Style.parse(color)
        _STYLE_CACHE[color] = cached
    return cached


# Pre-warm the palette so render_line never pays Style.parse cost.
for _hex in (
    "#444466",  # dust
    "#5599ff",  # rain
    "#ddddff",  # snow
    "#ffff66",  # lightning
    "#aaccff",  # steam
    "#ffcc44",  # sun
    "#ccccff",  # moon
    "#ffffee",  # star (brightest)
    "#ddddbb",  # star (mid-bright)
    "#aaaa88",  # star (mid-dim)
    "#777755",  # star (dimmest)
    "#aaaaaa",  # cloud
):
    style_for(_hex)


# --- Prop sprites ---
# Lines + a single color, drawn anchored to the buddy's current position.


@dataclass(frozen=True)
class PropSprite:
    lines: tuple[str, ...]
    color: str
    follows_buddy: bool  # True → cloud anchored above buddy; False → fixed sky corner
    invert: bool = False  # True → fg/bg swapped at render time

    @property
    def height(self) -> int:
        return len(self.lines)

    @property
    def width(self) -> int:
        return max((len(line) for line in self.lines), default=0)


SUN_SPRITE = PropSprite(
    lines=(
        "     ░▓▓░     ",
        "  █▓░▒▓▓▒░▓█  ",
        " ░░▒██████▒░░ ",
        "▒█░████████░█▒",
        " ░░▒██████▒░░ ",
        "  █▓░▒▓▓▒░▓█  ",
        "     ░▓▓░     ",
    ),
    color="#ffcc44",
    follows_buddy=False,
)


MOON_SPRITE = PropSprite(
    lines=(
      "     █████     ",
      "   ▓█░░░░████  ",
      "         ░███▒ ",
      "          ░███░",
      "          ▒████",
      "▒█▓      ▒████░",
      " ▒██░░░░████░  ",
      "   ▒██████▒    ",
    ),
    color="#ccccff",
    follows_buddy=False,
)


RAIN_CLOUD_SPRITE = PropSprite(
    lines=(
        r"  .--.   ",
        r" (    ).",
        r"(___.__)",
    ),
    color="#aaaaaa",
    follows_buddy=True,
)


def prop_for(env: EnvState) -> PropSprite | None:
    """Return the prop sprite for this environment. None when no prop applies."""
    if env.kind is Kind.CLEAR:
        return SUN_SPRITE if env.is_day else MOON_SPRITE
    if env.kind in (Kind.RAIN, Kind.DRIZZLE, Kind.STORM, Kind.SNOW):
        return RAIN_CLOUD_SPRITE
    return None
