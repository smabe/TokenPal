"""Hand-drawn ASCII character skeletons for voice buddies.

Each skeleton is a 14-line Rich-markup template with format slots for a
palette + face glyphs. The voice-training classifier picks a skeleton +
palette for a character; ``render`` substitutes the slots to produce
idle / idle_alt / talking frames.

Palette slots (all keys in ``PALETTE_KEYS`` are required):
    {hair}    opening tag for hair color, e.g. "[#ffcc44]"
    {skin}    opening tag for skin tone
    {outfit}  opening tag for primary clothing color
    {accent}  opening tag for accessory / trim color
    {shadow}  opening tag for darker shading
    {c}       closing tag "[/]"
    {eye}     single glyph (e.g. "◉"; "─" for blink)
    {mouth}   single glyph (e.g. "▽" neutral, "◇" talking)

Convention: margin spaces live OUTSIDE the color tags and art glyphs
live INSIDE, so every row's width is trivially
``len(margin_left) + len(art_glyphs) + len(margin_right)``. ``render``
centers each line to ``CELL_WIDTH`` so the Textual overlay never jitters
between rows.

Preview:
    .venv/bin/python -m tokenpal.ui.ascii_skeletons
"""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

from tokenpal.ui.ascii_zones import (
    apply_replace_zones,
    headwear_prefix,
    normalize_zones,
    trailing_suffix,
)

# --- humanoid-tall: standard hero/adventurer build ---
# Finn, Mordecai, Marco, generic protagonist.
HUMANOID_TALL = """\
{hair}▄▄▄▄▄▄▄▄▄▄▄{c}
{hair}▄█▓▓▓▓▓▓▓▓▓▓▓▓▓█▄{c}
{hair}█▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓█{c}
{hair}█▓▓▓{c}{skin}▓▓▓▓▓▓▓▓▓{c}{hair}▓▓▓█{c}
{skin}█▓▓▓{eye}▓▓▓▓▓▓▓{eye}▓▓▓█{c}
{skin}█▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓█{c}
{skin}█▓▓▓▓▓▓▓{mouth}▓▓▓▓▓▓▓█{c}
{skin}▀▄▄▄▄▄▄▄▄▄▄▄▄▄▀{c}
{outfit}▄███████████████▄{c}
{outfit}█▓▓▓{c}{accent}◆{c}{outfit}▓▓▓▓▓▓▓{c}{accent}◆{c}{outfit}▓▓▓█{c}
{outfit}█▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓█{c}
{outfit}██▄▄▄▄▄▄▄▄▄▄▄██{c}
{outfit}██         ██{c}
{shadow}▀▀         ▀▀{c}
"""


# --- humanoid-stocky: short/chunky build ---
# Wider shoulders, squat legs. Dexter-ish, compact bro characters.
HUMANOID_STOCKY = """\
{hair}▄▄▄▄▄▄▄▄▄{c}
{hair}▄█▓▓▓▓▓▓▓▓▓▓▓█▄{c}
{hair}█▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓█{c}
{skin}█▓▓▓{eye}▓▓▓▓▓▓▓▓▓{eye}▓▓▓█{c}
{skin}█▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓█{c}
{skin}█▓▓▓▓▓▓▓▓{mouth}▓▓▓▓▓▓▓▓█{c}
{skin}▀▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▀{c}
{outfit}▄███████████████████▄{c}
{outfit}█▓▓▓{c}{accent}◆{c}{outfit}▓▓▓▓▓▓▓▓▓▓▓{c}{accent}◆{c}{outfit}▓▓▓█{c}
{outfit}█▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓█{c}
{outfit}█▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓█{c}
{outfit}██▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄██{c}
{outfit}████         ████{c}
{shadow}▀▀▀           ▀▀▀{c}
"""


# --- robot-boxy: rectangular robot body ---
# BMO / Bender / classic tin-can robot. Head and body are both boxes but
# differ in size (body wider) so the silhouette reads as a robot, not a
# single rectangle.
ROBOT_BOXY = """\
{accent}▄   ▄{c}
{accent}█   █{c}
{outfit}▄███████████████▄{c}
{outfit}█▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓█{c}
{outfit}█▓▓ {c}{accent}{eye}{c}{outfit}       {c}{accent}{eye}{c}{outfit} ▓▓█{c}
{outfit}█▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓█{c}
{outfit}█▓▓▓▓▓▓ {c}{accent}{mouth}{c}{outfit} ▓▓▓▓▓▓█{c}
{outfit}█▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓█{c}
{outfit}▄███████████████████▄{c}
{outfit}█▓▓{c}{accent}◆{c}{outfit}▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓{c}{accent}◆{c}{outfit}▓▓█{c}
{outfit}█▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓█{c}
{outfit}█▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓█{c}
{outfit}█▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄█{c}
{shadow}██                 ██{c}
"""


# --- creature-small: tiny round chibi body ---
# BMO-adjacent cubes, Nibbler, pet-sized characters.
CREATURE_SMALL = """\

{hair}▄▄▄▄▄▄▄▄▄{c}
{hair}▄█▓▓▓▓▓▓▓▓▓▓▓█▄{c}
{hair}█▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓█{c}
{skin}█▓▓{eye}▓▓▓▓▓▓▓▓▓{eye}▓▓█{c}
{skin}█▓▓▓▓▓▓▓{mouth}▓▓▓▓▓▓▓█{c}
{skin}▀▄▄▄▄▄▄▄▄▄▄▄▄▄▀{c}
{outfit}▄█████████████▄{c}
{outfit}█▓▓▓{c}{accent}◆{c}{outfit}▓▓▓▓▓▓▓▓▓█{c}
{outfit}█▓▓▓▓▓▓▓▓▓▓▓▓▓█{c}
{outfit}██▄▄▄▄▄▄▄▄▄██{c}
{outfit}██       ██{c}
{shadow}▀▀       ▀▀{c}

"""


# --- mystical-cloaked: wizard / jester / hooded figure ---
# Ice King, Prismo-ish, generic sorcerer. Hood peak, hood shadow over eyes,
# and a robe that flares wider at the bottom.
MYSTICAL_CLOAKED = """\
{accent}▄█▄{c}
{accent}▄███▄{c}
{hair}▄█▓▓▓▓▓▓▓█▄{c}
{hair}▄█▓▓▓▓▓▓▓▓▓▓▓█▄{c}
{hair}█▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓█{c}
{hair}█▓▓▓{c}{shadow}▓▓▓▓▓▓▓▓▓{c}{hair}▓▓▓█{c}
{skin}█▓▓▓{eye}▓▓▓▓▓▓▓{eye}▓▓▓█{c}
{skin}█▓▓▓▓▓▓▓{mouth}▓▓▓▓▓▓▓█{c}
{hair}▀▄▄▄▄▄▄▄▄▄▄▄▄▄▀{c}
{outfit}▄▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▄{c}
{outfit}▓▓▓▓▓{c}{accent}◆{c}{outfit}▓▓▓▓▓{c}{accent}◆{c}{outfit}▓▓▓▓▓{c}
{outfit}▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓{c}
{outfit}▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓{c}
{shadow}▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀{c}
"""


# --- ghost-floating: hovering, no legs, wispy fade at the bottom ---
# Ghosts, spirits, floating orbs. The gradient ▓▒░ at the bottom fades
# into translucent wisps so the character reads as "not standing on
# anything".
GHOST_FLOATING = """\

{hair}▄▄▄▄▄▄▄▄▄▄▄{c}
{hair}▄█▓▓▓▓▓▓▓▓▓▓▓▓▓█▄{c}
{hair}█▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓█{c}
{skin}█▓▓▓{eye}▓▓▓▓▓▓▓{eye}▓▓▓█{c}
{skin}█▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓█{c}
{skin}█▓▓▓▓▓▓▓{mouth}▓▓▓▓▓▓▓█{c}
{skin}█▓▓▓{c}{accent}◆{c}{skin}▓▓▓▓▓▓▓{c}{accent}◆{c}{skin}▓▓▓█{c}
{skin}▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓{c}
{shadow}▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒{c}
{shadow}░░░░░░░░░░░░░░░░░{c}
{shadow}░░░ ░░░ ░░░ ░░░{c}
{shadow}░    ░    ░    ░{c}

"""


# --- animal-quadruped: 4-legged pet/creature ---
# Jake in dog form, Nibbler-style small pets. Front-facing chibi with two
# ears up top and four stubby legs visible at the bottom.
ANIMAL_QUADRUPED = """\

{hair}▄▄▄     ▄▄▄{c}
{hair}█▓█     █▓█{c}
{hair}▄█▓▓▓▄▄▄▓▓▓█▄{c}
{hair}█▓▓▓▓▓▓▓▓▓▓▓▓▓█{c}
{skin}█▓▓▓{eye}▓▓▓▓▓▓▓{eye}▓▓▓█{c}
{skin}█▓▓▓▓▓▓▓{mouth}▓▓▓▓▓▓█{c}
{skin}▀▄▄▄▄▄▄▄▄▄▄▄▄▀{c}
{outfit}▄▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▄{c}
{outfit}▓▓▓{c}{accent}◆{c}{outfit}▓▓▓▓▓▓▓▓▓▓▓{c}{accent}◆{c}{outfit}▓▓▓{c}
{outfit}▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓{c}
{outfit}██  ██       ██  ██{c}
{outfit}██  ██       ██  ██{c}
{shadow}▀▀  ▀▀       ▀▀  ▀▀{c}
"""


# --- winged: humanoid with wings flared behind shoulders ---
# Angels, Prismo-adjacent, fairies, Icarus-types. Wings flare outward
# with feather/scale texture in the accent color; body stays humanoid.
WINGED = """\
{hair}▄▄▄▄▄▄▄▄▄▄▄{c}
{hair}▄█▓▓▓▓▓▓▓▓▓▓▓▓▓█▄{c}
{hair}█▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓█{c}
{skin}█▓▓▓{eye}▓▓▓▓▓▓▓{eye}▓▓▓█{c}
{skin}█▓▓▓▓▓▓▓{mouth}▓▓▓▓▓▓▓█{c}
{skin}▀▄▄▄▄▄▄▄▄▄▄▄▄▄▀{c}
{accent}▄▀▀{c}{outfit}▄███████████▄{c}{accent}▀▀▄{c}
{accent}▓▓▓{c}{outfit}█▓▓{c}{accent}◆{c}{outfit}▓▓▓▓▓{c}{accent}◆{c}{outfit}▓▓█{c}{accent}▓▓▓{c}
{accent}▓▓▓▓{c}{outfit}█▓▓▓▓▓▓▓▓▓█{c}{accent}▓▓▓▓{c}
{accent}▀▓▓▓{c}{outfit}██▄▄▄▄▄▄▄██{c}{accent}▓▓▓▀{c}
{accent}▀{c}{outfit}██         ██{c}{accent}▀{c}
{outfit}██         ██{c}
{shadow}▀▀         ▀▀{c}

"""


# --- blob-amorphous: irregular, bumpy silhouette ---
# Lumpy Space Princess, talking food (Peppermint Butler, Cinnamon Bun).
# Asymmetric edges on purpose — no two rows share the same width — so the
# eye never reads this as a regular oval or cloud. Headwear compat keeps
# "crown" so LSP's star crown prepends above row 0.
BLOB_AMORPHOUS = """\
{hair}     ▄██▄▄          {c}
{hair}   ▄▇██████▄        {c}
{hair}  ▇██████████▇▅     {c}
{hair}▇████████████████   {c}
{hair}██████████████████  {c}
{hair}█████{c}{skin}{eye}▓▓▓▓▓▓▓▓{eye}{c}{hair}████{c}
{hair}████{c}{skin}▓▓▓▓▓▓▓▓▓▓▓▓{c}{hair}████{c}
{hair}████{c}{skin}▓▓▓▓{mouth}▓▓▓▓▓▓▓{c}{hair}████{c}
{hair}██████████████████  {c}
{hair}▀█████████████████  {c}
{hair}  ▀▀█████████████▀ {c}
{hair}     ▀▀███████▀▀   {c}
{hair}        ▀▀█▀▀      {c}
{shadow}          ▀▀        {c}
"""


# --- hand-creature: five-fingered palm with a face ---
# Hi Five Ghost, Thing, Rayman-style disembodied hand. Palm-forward
# orientation: five fingers rise above a wide palm whose face occupies
# the middle band; short stubby legs carry the body at the bottom.
HAND_CREATURE = """\
{hair}██ ██ ██ ██ ██   {c}
{hair}██ ██ ██ ██ ██   {c}
{hair}██ ██ ██ ██ ██   {c}
{hair}██ ██ ██ ██ ██   {c}
{hair}█████████████████{c}
{hair}█▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓█{c}
{hair}█▓▓▓{c}{skin}{eye}▓▓▓▓▓▓▓{eye}{c}{hair}▓▓▓█{c}
{hair}█▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓█{c}
{hair}█▓▓▓{c}{skin}▓▓▓▓{mouth}▓▓▓▓▓{c}{hair}▓▓▓█{c}
{hair}█▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓█{c}
{hair}██▄▄▄▄▄▄▄▄▄▄▄▄▄██{c}
{hair}█████████████████{c}
{hair}██           ██  {c}
{shadow}▀▀           ▀▀  {c}
"""


SKELETONS: dict[str, str] = {
    "humanoid_tall": HUMANOID_TALL,
    "humanoid_stocky": HUMANOID_STOCKY,
    "robot_boxy": ROBOT_BOXY,
    "creature_small": CREATURE_SMALL,
    "mystical_cloaked": MYSTICAL_CLOAKED,
    "ghost_floating": GHOST_FLOATING,
    "animal_quadruped": ANIMAL_QUADRUPED,
    "winged": WINGED,
    "blob_amorphous": BLOB_AMORPHOUS,
    "hand_creature": HAND_CREATURE,
}


# Palette keys every skeleton template references. The classifier in
# train_voice.py imports this to validate LLM output. ``highlight`` is
# the brighter-than-outfit tone used by zone overlays (crown gleam, wing
# sheen, etc.) — existing body templates don't reference it directly yet
# so older renders still work; it's additive.
PALETTE_KEYS: tuple[str, ...] = (
    "hair", "skin", "outfit", "accent", "shadow", "highlight",
)


# Sample palettes for the __main__ preview. Tests also import this.
_SAMPLE_PALETTES: dict[str, dict[str, str]] = {
    "humanoid_tall": {  # Finn-ish
        "hair": "[#ffffff]",       # white hat
        "skin": "[#f4d4a8]",       # pale skin
        "outfit": "[#3da8e8]",     # blue shirt
        "accent": "[#ffd700]",     # gold buttons
        "shadow": "[#2a6fa5]",
        "highlight": "[#ffffff]",
        "eye": "●",
        "mouth": "▽",
    },
    "humanoid_stocky": {  # Dexter-ish
        "hair": "[#ff8800]",       # orange hair
        "skin": "[#f4d4a8]",
        "outfit": "[#ffffff]",     # lab coat
        "accent": "[#cccccc]",
        "shadow": "[#888888]",
        "highlight": "[#ffffff]",
        "eye": "◉",
        "mouth": "▽",
    },
    "robot_boxy": {  # BMO-ish (less Bender)
        "hair": "[#aaaaaa]",
        "skin": "[#aaaaaa]",
        "outfit": "[#6dbb5c]",     # BMO green
        "accent": "[#ff5555]",
        "shadow": "[#2e5a26]",
        "highlight": "[#ffffff]",
        "eye": "◉",
        "mouth": "═",
    },
    "creature_small": {  # Nibbler-ish
        "hair": "[#b87cd4]",       # purple ears
        "skin": "[#b87cd4]",
        "outfit": "[#8a5aa6]",
        "accent": "[#ffd700]",
        "shadow": "[#4e2e5e]",
        "highlight": "[#ffffff]",
        "eye": "●",
        "mouth": "ᗣ",
    },
    "mystical_cloaked": {  # Ice King-ish
        "hair": "[#dddddd]",       # white beard
        "skin": "[#c0dffb]",       # blue skin
        "outfit": "[#4a3a7a]",     # purple robe
        "accent": "[#ffd700]",     # gold trim
        "shadow": "[#241a3a]",
        "highlight": "[#ffffff]",
        "eye": "●",
        "mouth": "▽",
    },
    "ghost_floating": {  # classic friendly ghost
        "hair": "[#eeeeee]",       # white halo/top
        "skin": "[#eeeeee]",       # white body
        "outfit": "[#cccccc]",     # (unused)
        "accent": "[#7ab8ff]",     # blue spooky accents
        "shadow": "[#888888]",     # fading wisps
        "eye": "●",
        "mouth": "◡",
    },
    "animal_quadruped": {  # Jake-in-dog-form
        "hair": "[#ffb84d]",       # golden fur
        "skin": "[#ffb84d]",       # same as fur (chibi)
        "outfit": "[#e09638]",     # darker belly fur
        "accent": "[#cc5500]",     # collar
        "shadow": "[#804a1e]",
        "highlight": "[#ffffff]",
        "eye": "●",
        "mouth": "ᗣ",
    },
    "winged": {  # angel / Prismo-adjacent
        "hair": "[#ffe89b]",       # golden halo
        "skin": "[#f4d4a8]",
        "outfit": "[#ffffff]",     # white robe
        "accent": "[#e0e0ff]",     # silver-blue wing feathers
        "shadow": "[#888888]",
        "highlight": "[#ffffff]",
        "eye": "●",
        "mouth": "▽",
    },
    "blob_amorphous": {  # Lumpy Space Princess-ish
        "hair": "[#a78bfa]",       # LSP purple body
        "skin": "[#c9b3ff]",       # lighter purple belly for face contrast
        "outfit": "[#7d5ed8]",     # shade below
        "accent": "[#ffd700]",     # gold star (zone-overlay only)
        "shadow": "[#4d3580]",
        "highlight": "[#e2d7ff]",
        "eye": "●",
        "mouth": "▽",
    },
    "hand_creature": {  # Hi Five Ghost-ish
        "hair": "[#ffffff]",       # white body/fingers
        "skin": "[#f2f2f2]",       # faint gray face for contrast
        "outfit": "[#dddddd]",
        "accent": "[#aaaaaa]",
        "shadow": "[#888888]",
        "highlight": "[#ffffff]",
        "eye": "●",
        "mouth": "◡",
    },
}


CELL_WIDTH = 29


def _pad_line(line: str, width: int = CELL_WIDTH) -> str:
    """Center-pad a rendered line to ``width`` visible cells.

    Only pads; never trims, since trimming could truncate a markup tag
    and crash the parser. Over-wide lines are returned unchanged so
    template bugs fail loudly in the preview rather than silently.
    """
    cur = Text.from_markup(line).cell_len
    if cur >= width:
        return line
    slack = width - cur
    left = slack // 2
    right = slack - left
    return " " * left + line + " " * right


def render(
    skeleton_name: str,
    palette: dict[str, str],
    zones: dict[str, str] | None = None,
) -> list[str]:
    """Substitute palette + glyphs into a skeleton template.

    Returns the list of markup lines, each normalized to ``CELL_WIDTH``
    visible cells so the Textual overlay has zero edge-jitter between
    rows. Missing palette slots raise KeyError so bad palettes fail
    loudly in tests.

    When ``zones`` carries ``headwear`` (or other future zone picks), the
    matching overlay from ``ascii_zones`` is prepended to the rendered
    frame. Total height grows by the overlay's row count; blink + talking
    frames stay aligned because the overlay is constant across variants.
    """
    template = SKELETONS[skeleton_name]
    slots = {"c": "[/]", **palette}
    normalized = normalize_zones(skeleton_name, zones or {})
    prefix_rows = headwear_prefix(normalized.get("headwear", "none"), slots)
    suffix_rows = trailing_suffix(
        normalized.get("trailing", "none"), skeleton_name, slots,
    )
    # splitlines preserves leading/trailing blanks (e.g. creature_small uses
    # blank rows as padding) where rstrip+split would drop the trailing one.
    body_rows = template.format(**slots).splitlines()
    body_rows = apply_replace_zones(body_rows, skeleton_name, normalized, slots)
    return [_pad_line(line) for line in prefix_rows + body_rows + suffix_rows]


def _preview() -> None:
    """Render every skeleton with its sample palette to stdout via Rich."""
    console = Console()
    for name, palette in _SAMPLE_PALETTES.items():
        console.rule(f"[bold]{name}")
        for line in render(name, palette):
            console.print(line, markup=True, highlight=False)
        console.print()


if __name__ == "__main__":
    _preview()
