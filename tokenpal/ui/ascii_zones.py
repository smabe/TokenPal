"""Addressable zones layered on top of skeleton templates.

A zone is a named region a classifier can fill with curated content to
make a character read. Zones come in two modes:

- ``prepend``: overlay rows added above the skeleton template
  (headwear sits here — crown, hood_with_ears, antenna).
- ``replace``: overlay rows overwrite specific row ranges of the
  rendered skeleton body (facial_hair sits here — an Ice King beard
  eats the top of the outfit rows).

Every zone option is a hand-drawn micro-template referencing the same
palette slots the main skeletons use ({hair}, {outfit}, etc.). A
per-skeleton compat set coerces illegal picks to ``"none"``; a per-
(zone, skeleton) target-row table tells the replace-mode renderer which
rows each zone owns so no two zones collide.

Overlay shapes are asymmetric by design: prepend overlays are keyed by
option only (``HEADWEAR_OVERLAYS[option] -> template``) because a
headwear prefix floats above every skeleton without shoulder math.
Replace overlays are keyed by option AND skeleton
(``FACIAL_HAIR_OVERLAYS[option][skeleton] -> template``) because an
Ice King beard has to fit a wider robe than a humanoid-tall torso.
"""

from __future__ import annotations

from typing import Literal

ZoneMode = Literal["prepend", "replace"]

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


# Facial-hair overlays — replace-mode content that overwrites the rows
# named in ``_REPLACE_TARGETS``. Beard designs use hair color with a
# rectangular silhouette; the body base + legs render below untouched so
# shoulders and legs still read as body. Start minimal: beard_long for
# Ice King / wizards, beard_stubble for Hank Hill / Pops. Expand once
# real characters expose gaps.
FACIAL_HAIR_OVERLAYS: dict[str, dict[str, str]] = {
    "none": {
        "humanoid_tall": "",
        "mystical_cloaked": "",
    },
    "beard_long": {
        "humanoid_tall": (
            "{hair}▄█████████████{c}\n"
            "{hair}██████████████{c}\n"
            "{hair}████████████{c}\n"
        ),
        "mystical_cloaked": (
            "{hair}▄███████████████{c}\n"
            "{hair}████████████████{c}\n"
            "{hair}██████████████{c}\n"
        ),
    },
    "beard_stubble": {
        "humanoid_tall": (
            "{skin}▀▄{c}{shadow}▄▄▄▄▄▄▄▄▄{c}{skin}▄▀{c}\n"
        ),
        "mystical_cloaked": (
            "{hair}▀▄{c}{shadow}▄▄▄▄▄▄▄▄▄{c}{hair}▄▀{c}\n"
        ),
    },
}

FACIAL_HAIR_OPTIONS: tuple[str, ...] = tuple(FACIAL_HAIR_OVERLAYS.keys())


# Body-motif overlays — replace-mode content that rewrites a 2-row
# torso block with a recessed rectangle reading as screen / chest door
# / etc. Ships on robot_boxy only to start (BMO + Bender differ here
# despite sharing the same silhouette). Extend to humanoid_tall when a
# strap / belt / belly-stripe variant is actually needed.
BODY_MOTIF_OVERLAYS: dict[str, dict[str, str]] = {
    "none": {
        "robot_boxy": "",
    },
    "screen_dpad": {
        "robot_boxy": (
            "{outfit}█▓▓{c}{shadow}███████████████{c}{outfit}▓▓█{c}\n"
            "{outfit}█▓▓{c}{shadow}███████████████{c}{outfit}▓▓█{c}\n"
        ),
    },
    "chest_door": {
        "robot_boxy": (
            "{outfit}█▓▓▓▓▓▓▓{c}{shadow}█████{c}{outfit}▓▓▓▓▓▓▓█{c}\n"
            "{outfit}█▓▓▓▓▓▓▓{c}{shadow}█████{c}{outfit}▓▓▓▓▓▓▓█{c}\n"
        ),
    },
}

BODY_MOTIF_OPTIONS: tuple[str, ...] = tuple(BODY_MOTIF_OVERLAYS.keys())


BODY_MOTIF_RUBRIC: dict[str, str] = {
    "none": (
        "no chest/body detail (DEFAULT — most characters have plain "
        "torsos at this resolution)"
    ),
    "screen_dpad": (
        "wide recessed display panel taking up most of the chest, "
        "face-like (BMO's screen, living-gameboy characters)"
    ),
    "chest_door": (
        "small dark rectangular door/panel in the center of the chest "
        "(Bender's chest compartment)"
    ),
}


FACIAL_HAIR_RUBRIC: dict[str, str] = {
    "none": (
        "clean-shaven (DEFAULT — most characters, anyone whose chin is "
        "visible on screen)"
    ),
    "beard_long": (
        "long rectangular beard reaching down past the chest (Ice King, "
        "Gandalf, wizards, Hermes)"
    ),
    "beard_stubble": (
        "short stubble or short beard just under the mouth (Hank Hill, "
        "Pops, 5-o-clock shadow)"
    ),
}


# Zone-mode registry. Prepend zones add rows above the body; replace
# zones overwrite rows of the rendered body at fixed target-row ranges.
_ZONE_MODES: dict[str, ZoneMode] = {
    "headwear": "prepend",
    "facial_hair": "replace",
    "body_motif": "replace",
}


# Module-level replace-mode overlay dispatch — maps each replace-mode
# zone to its nested option→skeleton→template registry. Keep targets +
# overlays co-authored: if `_REPLACE_TARGETS[(zone, opt, skel)]` exists,
# `_REPLACE_OVERLAYS[zone][opt][skel]` MUST exist too, so the silent-
# skip in `apply_replace_zones` is defense-in-depth, not normal flow.
_REPLACE_OVERLAYS: dict[str, dict[str, dict[str, str]]] = {
    "facial_hair": FACIAL_HAIR_OVERLAYS,
    "body_motif": BODY_MOTIF_OVERLAYS,
}


# Per-(zone, option, skeleton) target-row range for replace-mode zones.
# ``(start, end)`` is a Python-slice half-open interval into the rendered
# body rows. Keyed by option, not just zone, because a 3-row long beard
# and a 1-row stubble chin occupy different ranges. Ranges for a given
# skeleton stay disjoint across zones by construction — facial_hair
# can't reach the eye row, eye_region (future) can't reach the chin.
_REPLACE_TARGETS: dict[tuple[str, str, str], tuple[int, int]] = {
    # beard_long: 3-row rectangle covering the chin fade + upper torso.
    # Legs + base (row 11+) still render so the standing silhouette
    # survives.
    ("facial_hair", "beard_long", "humanoid_tall"): (8, 11),
    ("facial_hair", "beard_long", "mystical_cloaked"): (8, 11),
    # beard_stubble: just the chin row, body below untouched.
    ("facial_hair", "beard_stubble", "humanoid_tall"): (7, 8),
    ("facial_hair", "beard_stubble", "mystical_cloaked"): (8, 9),
    # body_motif: center-torso 2-row rectangle. Rows 10-11 on robot_boxy
    # are plain {outfit} fill with no accent markers, so the motif lands
    # cleanly without fighting the ◆ rivets on row 9.
    ("body_motif", "screen_dpad", "robot_boxy"): (10, 12),
    ("body_motif", "chest_door", "robot_boxy"): (10, 12),
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
    "facial_hair": {
        "humanoid_tall": {"none", "beard_long", "beard_stubble"},
        "humanoid_stocky": {"none"},
        "robot_boxy": {"none"},
        "creature_small": {"none"},
        "mystical_cloaked": {"none", "beard_long", "beard_stubble"},
        "ghost_floating": {"none"},
        "animal_quadruped": {"none"},
        "winged": {"none"},
    },
    "body_motif": {
        "humanoid_tall": {"none"},
        "humanoid_stocky": {"none"},
        "robot_boxy": {"none", "screen_dpad", "chest_door"},
        "creature_small": {"none"},
        "mystical_cloaked": {"none"},
        "ghost_floating": {"none"},
        "animal_quadruped": {"none"},
        "winged": {"none"},
    },
}


def rubric_block(rubric: dict[str, str]) -> str:
    """Format a zone rubric for splicing into the classifier prompt."""
    return "".join(f"- {k}: {v}\n" for k, v in rubric.items()) + "\n"


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


def apply_replace_zones(
    body_rows: list[str],
    skeleton: str,
    zones: dict[str, str],
    slots: dict[str, str],
) -> list[str]:
    """Overwrite body rows for each active replace-mode zone.

    Walks ``zones`` and, for every key whose ``_ZONE_MODES`` entry is
    ``"replace"`` with a non-``"none"`` value, splices the rendered
    overlay into the row range declared in ``_REPLACE_TARGETS``. Zones
    with no target-row entry for this skeleton, unknown zone names, or
    empty overlays are left untouched so illegal combos degrade
    silently instead of crashing.
    """
    active = [
        (name, value) for name, value in zones.items()
        if _ZONE_MODES.get(name) == "replace" and value != "none"
    ]
    if not active:
        return body_rows
    out = list(body_rows)
    for zone_name, value in active:
        target = _REPLACE_TARGETS.get((zone_name, value, skeleton))
        if target is None:
            continue
        overlay_tmpl = (
            _REPLACE_OVERLAYS.get(zone_name, {}).get(value, {}).get(skeleton, "")
        )
        if not overlay_tmpl:
            continue
        overlay_rows = overlay_tmpl.format(**slots).splitlines()
        start, end = target
        out[start:end] = overlay_rows
    return out
