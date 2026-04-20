"""ASCII sprites + Style cache for the buddy environment overlay.

Hex-only colors — Rich-only color names crash Textual's stricter style parser
(see ascii_renderer._fix_markup for the same constraint elsewhere).
``Style.parse`` is the hot-path expense, not Segment construction; we build
every Style we need at import time and reuse them per-tick.
"""

from __future__ import annotations

import math
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
    anchor_dx: int = 0  # cell offset from computed anchor (for layered stacks)
    anchor_dy: int = 0
    # Horizontal drift amplitude in cells. 0 = stationary. When >0 the overlay
    # consults the shared CloudDrift clock and adds cos(phase + offset) * amp
    # to anchor_x each tick, letting two sprites share a clock but move
    # anti-phase via different drift_phase_offset values.
    drift_x_amplitude: float = 0.0
    drift_phase_offset: float = 0.0

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


# Overcast clouds layered in front of the sun. Same width as SUN_SPRITE so the
# fixed sky-corner anchor lines up; anchor_dy pushes them down so they cover
# the sun's dense middle rows while leaving the top rays peeking out. Two
# sprites share one CloudDrift clock at anti-phase (π apart) so they always
# move in opposite directions — reads as two clouds passing each other past
# the sun.
_OVERCAST_CLOUD_LINES = (
    "  ░░▒▒▒▒▒▒░░  ",
    " ░▒▒▓▓▓▓▓▓▒▒░ ",
    "▒▓▓▓██████▓▓▓▒",
    " ░▒▒▓▓▓▓▓▓▒▒░ ",
)

OVERCAST_CLOUD_A = PropSprite(
    lines=_OVERCAST_CLOUD_LINES,
    color="#aaaaaa",
    follows_buddy=False,
    anchor_dy=2,
    drift_x_amplitude=4.0,
    drift_phase_offset=0.0,
)

OVERCAST_CLOUD_B = PropSprite(
    lines=_OVERCAST_CLOUD_LINES,
    color="#aaaaaa",
    follows_buddy=False,
    anchor_dx=-6,
    anchor_dy=3,
    drift_x_amplitude=4.0,
    drift_phase_offset=math.pi,
)


# Overcast threshold on Kind.CLOUDY intensity. WMO 3 maps to 0.8 (overcast);
# WMO 2 maps to 0.4 (partly cloudy) — below this we leave the sky empty so
# light-cloud days don't claim the same visual as full overcast.
_OVERCAST_INTENSITY = 0.7


def props_for(env: EnvState) -> tuple[PropSprite, ...]:
    """Return 0+ sprites to stack for this environment, drawn back-to-front."""
    if env.kind is Kind.CLEAR:
        return (SUN_SPRITE if env.is_day else MOON_SPRITE,)
    if env.kind is Kind.CLOUDY:
        if env.is_day and env.intensity >= _OVERCAST_INTENSITY:
            return (SUN_SPRITE, OVERCAST_CLOUD_A, OVERCAST_CLOUD_B)
        return ()
    if env.kind in (Kind.RAIN, Kind.DRIZZLE, Kind.STORM, Kind.SNOW):
        return (RAIN_CLOUD_SPRITE,)
    return ()


def prop_for(env: EnvState) -> PropSprite | None:
    """Back-compat shim — returns the first sprite from :func:`props_for`."""
    stack = props_for(env)
    return stack[0] if stack else None
