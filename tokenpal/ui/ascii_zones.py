"""Addressable zones layered on top of skeleton templates.

A zone is a named region a classifier can fill with curated content to
make a character read. V1 ships one zone (``headwear``) which prepends
extra rows to a skeleton. Later zones (facial_hair, eye_region,
body_motif, trailing) will follow the same pattern — small enum of
hand-drawn micro-templates, per-skeleton compatibility table, silent
coercion to ``"none"`` when an illegal combo sneaks through.

Keeping headwear as a prefix instead of an in-template slot means the
existing 8 skeletons stay untouched; only ``render()`` grows a ``zones``
parameter. Rendered height becomes 14-17 rows depending on pick.
"""

from __future__ import annotations

# Headwear overlays — multi-line Rich-markup strings prepended above a
# skeleton's first row. Each uses the palette slots the renderer
# substitutes ({hair}, {accent}, etc.). ``none`` is the empty string;
# never ``None`` so the format() call stays uniform.
HEADWEAR_OVERLAYS: dict[str, str] = {
    "none": "",
    "crown": (
        "{accent}▃▅█▅▃{c}\n"
        "{accent}▀▀▀▀▀{c}\n"
    ),
    "hood_with_ears": (
        "{hair}▄▄        ▄▄{c}\n"
        "{hair}██        ██{c}\n"
    ),
    "antenna": (
        "{shadow}│{c}\n"
        "{accent}◉{c}\n"
    ),
    "halo": (
        "{accent}◯{c}\n"
    ),
    "wizard_hat": (
        "{accent}▲{c}\n"
        "{accent}▞▚{c}\n"
        "{accent}▓▓▓{c}\n"
    ),
    "spikes": (
        "{hair}▲ ▲ ▲ ▲ ▲{c}\n"
        "{hair}█ █ █ █ █{c}\n"
    ),
}

HEADWEAR_OPTIONS: tuple[str, ...] = tuple(HEADWEAR_OVERLAYS.keys())


# Classifier-prompt rubric for each headwear option. Kept next to
# HEADWEAR_OVERLAYS so adding a new option is a single-file edit: append
# to both dicts, extend the per-skeleton compat set, done.
HEADWEAR_RUBRIC: dict[str, str] = {
    "none": (
        "no hat, no crown, no antenna (DEFAULT — Mordecai, Muscle Man, "
        "most ordinary characters)"
    ),
    "crown": "gold/jeweled crown (Ice King, Princess Bubblegum)",
    "hood_with_ears": (
        "rounded hood with two ear stubs on top (Finn the Human, "
        "bear-ear hoods)"
    ),
    "antenna": "thin wire + ball on top of a robot head (Bender)",
    "halo": "floating ring above the head (angels, Prismo-ish)",
    "wizard_hat": "tall pointed peak (wizards, witch hats)",
    "spikes": "3-5 sharp hair spikes (Rick Sanchez-style)",
}


# Per-skeleton compatibility — values not listed for a skeleton get
# coerced to ``"none"``. Permissive by default; only physical hats on
# floating/amorphous bodies are blocked.
_ZONE_COMPAT: dict[str, dict[str, set[str]]] = {
    "headwear": {
        "humanoid_tall": {
            "none", "crown", "hood_with_ears", "halo", "wizard_hat", "spikes",
        },
        "humanoid_stocky": {
            "none", "crown", "hood_with_ears", "halo", "wizard_hat", "spikes",
        },
        "robot_boxy": {"none", "antenna", "halo", "crown", "spikes"},
        "creature_small": {
            "none", "hood_with_ears", "antenna", "halo", "crown", "spikes",
        },
        "mystical_cloaked": {"none", "wizard_hat", "crown", "halo"},
        "ghost_floating": {"none", "halo", "crown"},
        "animal_quadruped": {"none", "hood_with_ears", "halo", "crown"},
        "winged": {"none", "halo", "crown", "hood_with_ears"},
    },
}


def normalize_zones(skeleton: str, zones: dict[str, str]) -> dict[str, str]:
    """Coerce zone picks to legal values for the given skeleton.

    Any zone key not declared in ``_ZONE_COMPAT`` is dropped. Any value
    not in the skeleton's allowed set becomes ``"none"``. Missing keys
    default to ``"none"`` so the renderer can always format() cleanly.
    """
    out: dict[str, str] = {}
    for zone_name, compat in _ZONE_COMPAT.items():
        allowed = compat.get(skeleton, {"none"})
        pick = zones.get(zone_name, "none")
        out[zone_name] = pick if pick in allowed else "none"
    return out


def headwear_prefix(
    headwear: str, slots: dict[str, str],
) -> list[str]:
    """Return the rendered headwear rows for a skeleton prefix.

    Empty list when ``headwear`` is ``"none"`` or unknown. Any template
    literal missing from the slot dict raises KeyError so palette gaps
    fail loudly in tests instead of rendering broken markup.
    """
    tmpl = HEADWEAR_OVERLAYS.get(headwear, "")
    if not tmpl:
        return []
    return tmpl.format(**slots).splitlines()
