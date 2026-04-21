"""Addressable zones layered on top of skeleton templates.

A zone is a named region a classifier can fill with curated content to
make a character read. Zones come in three modes:

- ``prepend``: overlay rows added above the skeleton template
  (headwear sits here — crown, hood_with_ears, antenna).
- ``replace``: overlay rows overwrite specific row ranges of the
  rendered skeleton body (facial_hair sits here — an Ice King beard
  eats the top of the outfit rows).
- ``append``: overlay rows are added below the body (tails, trailing
  hair wisps).

Each zone is declared as a single ``ZoneSpec`` instance bundling its
overlays, rubric, compat, and replace targets. Module-level constants
like ``HEADWEAR_OVERLAYS`` / ``FACIAL_HAIR_RUBRIC`` remain as aliases
pointing into the spec so existing imports in tests and train_voice
keep working. Registries (``_ZONE_MODES``, ``_REPLACE_OVERLAYS``,
``_APPEND_OVERLAYS``, ``_REPLACE_TARGETS``, ``_ZONE_COMPAT``) are
auto-derived from ``_ZONES`` so adding a sixth zone is a single
``ZoneSpec(...)`` definition + one ``_ZONES`` append.

Overlay shapes are asymmetric by design: prepend overlays are keyed by
option only (``overlays[option] -> template``) because a headwear
prefix floats above every skeleton without shoulder math. Replace and
append overlays are keyed by option AND skeleton
(``overlays[option][skeleton] -> template``) because the overlay has
to fit the skeleton's footprint.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ZoneMode = Literal["prepend", "replace", "append"]


@dataclass(frozen=True)
class ZoneSpec:
    """Single-object description of a zone's overlays, rubric, compat.

    ``overlays`` shape depends on ``mode``:
      - ``prepend``: ``dict[option, template]``
      - ``replace`` / ``append``: ``dict[option, dict[skeleton, template]]``

    ``targets`` is replace-mode only: ``(option, skeleton) -> (start, end)``
    row range (half-open). Empty for prepend/append.
    """

    name: str
    mode: ZoneMode
    # Prepend: dict[str, str]. Replace/append: dict[str, dict[str, str]].
    # Can't type-narrow without splitting the class; rely on mode at call sites.
    overlays: dict[str, Any]
    rubric: dict[str, str]
    compat: dict[str, set[str]]
    targets: dict[tuple[str, str], tuple[int, int]] = field(default_factory=dict)

    @property
    def options(self) -> tuple[str, ...]:
        return tuple(self.overlays.keys())

    def rubric_block_for_prompt(self) -> str:
        """Format this zone's rubric for splicing into the classifier prompt."""
        return rubric_block(self.rubric)


# ---------------------------------------------------------------
# Zone: headwear (prepend)
# ---------------------------------------------------------------
HEADWEAR = ZoneSpec(
    name="headwear",
    mode="prepend",
    overlays={
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
    },
    rubric={
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
    },
    compat={
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
)


# ---------------------------------------------------------------
# Zone: facial_hair (replace)
# ---------------------------------------------------------------
FACIAL_HAIR = ZoneSpec(
    name="facial_hair",
    mode="replace",
    overlays={
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
    },
    rubric={
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
    },
    compat={
        "humanoid_tall": {"none", "beard_long", "beard_stubble"},
        "humanoid_stocky": {"none"},
        "robot_boxy": {"none"},
        "creature_small": {"none"},
        "mystical_cloaked": {"none", "beard_long", "beard_stubble"},
        "ghost_floating": {"none"},
        "animal_quadruped": {"none"},
        "winged": {"none"},
    },
    targets={
        # beard_long: 3-row rectangle covering the chin fade + upper torso.
        ("beard_long", "humanoid_tall"): (8, 11),
        ("beard_long", "mystical_cloaked"): (8, 11),
        # beard_stubble: just the chin row, body below untouched.
        ("beard_stubble", "humanoid_tall"): (7, 8),
        ("beard_stubble", "mystical_cloaked"): (8, 9),
    },
)


# ---------------------------------------------------------------
# Zone: body_motif (replace)
# ---------------------------------------------------------------
BODY_MOTIF = ZoneSpec(
    name="body_motif",
    mode="replace",
    overlays={
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
    },
    rubric={
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
    },
    compat={
        "humanoid_tall": {"none"},
        "humanoid_stocky": {"none"},
        "robot_boxy": {"none", "screen_dpad", "chest_door"},
        "creature_small": {"none"},
        "mystical_cloaked": {"none"},
        "ghost_floating": {"none"},
        "animal_quadruped": {"none"},
        "winged": {"none"},
    },
    targets={
        # center-torso 2-row rectangle. Rows 10-11 on robot_boxy are plain
        # {outfit} fill with no accent markers, so the motif lands cleanly.
        ("screen_dpad", "robot_boxy"): (10, 12),
        ("chest_door", "robot_boxy"): (10, 12),
    },
)


# ---------------------------------------------------------------
# Zone: eye_region (replace)
# ---------------------------------------------------------------
EYE_REGION = ZoneSpec(
    name="eye_region",
    mode="replace",
    overlays={
        "none": {
            "humanoid_tall": "",
            "animal_quadruped": "",
        },
        "single_cyclops": {
            "humanoid_tall": (
                "{skin}█▓▓▓{c}{highlight}███{c}{shadow}███{c}"
                "{highlight}███{c}{skin}▓▓▓█{c}\n"
            ),
        },
        "oversized_spiral": {
            "animal_quadruped": (
                "{skin}█▓▓{c}{accent}◉◎◉{c}{skin}▓▓▓▓▓{c}"
                "{accent}◉◎◉{c}{skin}▓▓█{c}\n"
            ),
        },
    },
    rubric={
        "none": (
            "standard two dot eyes (DEFAULT — almost every character)"
        ),
        "single_cyclops": (
            "one huge centered eye instead of two (Leela from Futurama, "
            "Kyubey-style single-eye creatures)"
        ),
        "oversized_spiral": (
            "giant bulging spiral / hypno eyes filling most of the face "
            "(Hypnotoad, hypno-frog characters)"
        ),
    },
    compat={
        "humanoid_tall": {"none", "single_cyclops"},
        "humanoid_stocky": {"none"},
        "robot_boxy": {"none"},
        "creature_small": {"none"},
        "mystical_cloaked": {"none"},
        "ghost_floating": {"none"},
        "animal_quadruped": {"none", "oversized_spiral"},
        "winged": {"none"},
    },
    targets={
        # single-row replace of the eye row. humanoid_tall row 4 and
        # animal_quadruped row 5 both carry the two {eye} slots.
        ("single_cyclops", "humanoid_tall"): (4, 5),
        ("oversized_spiral", "animal_quadruped"): (5, 6),
    },
)


# ---------------------------------------------------------------
# Zone: trailing (append)
# ---------------------------------------------------------------
TRAILING = ZoneSpec(
    name="trailing",
    mode="append",
    overlays={
        "none": {
            "animal_quadruped": "",
            "ghost_floating": "",
        },
        "tail_curly": {
            "animal_quadruped": (
                "{hair}   ∿∿∿∿{c}\n"
            ),
        },
        "hair_drift": {
            "ghost_floating": (
                "{hair}  ░  ░  ░  ░  {c}\n"
            ),
        },
    },
    rubric={
        "none": (
            "no trailing element (DEFAULT — almost every character)"
        ),
        "tail_curly": (
            "ringed/striped tail curling behind the body (Rigby, raccoon "
            "and squirrel characters)"
        ),
        "hair_drift": (
            "floating hair/wisps drifting down from a hovering body "
            "(Marceline's hair, floating spirits)"
        ),
    },
    compat={
        "humanoid_tall": {"none"},
        "humanoid_stocky": {"none"},
        "robot_boxy": {"none"},
        "creature_small": {"none"},
        "mystical_cloaked": {"none"},
        "ghost_floating": {"none", "hair_drift"},
        "animal_quadruped": {"none", "tail_curly"},
        "winged": {"none"},
    },
)


# Master list: order is prompt order for the classifier. Appending a new
# ZoneSpec here is all it takes to register a zone — the derived
# registries below pick it up automatically.
_ZONES: list[ZoneSpec] = [HEADWEAR, FACIAL_HAIR, BODY_MOTIF, EYE_REGION, TRAILING]


# ---------------------------------------------------------------
# Backward-compat aliases
# Many tests and train_voice import the legacy module-level constants
# by name. Keep them as aliases so existing call sites don't break.
# ---------------------------------------------------------------
HEADWEAR_OVERLAYS: dict[str, str] = HEADWEAR.overlays
HEADWEAR_OPTIONS: tuple[str, ...] = HEADWEAR.options
HEADWEAR_RUBRIC: dict[str, str] = HEADWEAR.rubric

FACIAL_HAIR_OVERLAYS: dict[str, dict[str, str]] = FACIAL_HAIR.overlays
FACIAL_HAIR_OPTIONS: tuple[str, ...] = FACIAL_HAIR.options
FACIAL_HAIR_RUBRIC: dict[str, str] = FACIAL_HAIR.rubric

BODY_MOTIF_OVERLAYS: dict[str, dict[str, str]] = BODY_MOTIF.overlays
BODY_MOTIF_OPTIONS: tuple[str, ...] = BODY_MOTIF.options
BODY_MOTIF_RUBRIC: dict[str, str] = BODY_MOTIF.rubric

EYE_REGION_OVERLAYS: dict[str, dict[str, str]] = EYE_REGION.overlays
EYE_REGION_OPTIONS: tuple[str, ...] = EYE_REGION.options
EYE_REGION_RUBRIC: dict[str, str] = EYE_REGION.rubric

TRAILING_OVERLAYS: dict[str, dict[str, str]] = TRAILING.overlays
TRAILING_OPTIONS: tuple[str, ...] = TRAILING.options
TRAILING_RUBRIC: dict[str, str] = TRAILING.rubric


# ---------------------------------------------------------------
# Derived registries
# Auto-built from _ZONES so adding a zone is a single dataclass edit.
# ---------------------------------------------------------------
_ZONE_MODES: dict[str, ZoneMode] = {z.name: z.mode for z in _ZONES}

_REPLACE_OVERLAYS: dict[str, dict[str, dict[str, str]]] = {
    z.name: z.overlays for z in _ZONES if z.mode == "replace"
}

_APPEND_OVERLAYS: dict[str, dict[str, dict[str, str]]] = {
    z.name: z.overlays for z in _ZONES if z.mode == "append"
}

_REPLACE_TARGETS: dict[tuple[str, str, str], tuple[int, int]] = {
    (z.name, opt, skel): rng
    for z in _ZONES if z.mode == "replace"
    for (opt, skel), rng in z.targets.items()
}

_ZONE_COMPAT: dict[str, dict[str, set[str]]] = {z.name: z.compat for z in _ZONES}


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


def trailing_suffix(
    trailing: str, skeleton: str, slots: dict[str, str],
) -> list[str]:
    """Return rendered rows appended BELOW a skeleton's body.

    Mirror of ``headwear_prefix`` for append-mode zones. The trailing
    overlays live in a zone→option→skeleton nested dict because a tail
    has to match the skeleton's footprint (a quadruped tail vs a ghost
    wisp drift different in shape).
    """
    tmpl = TRAILING_OVERLAYS.get(trailing, {}).get(skeleton, "")
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
