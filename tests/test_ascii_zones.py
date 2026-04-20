"""Unit tests for ascii_zones headwear overlay + compat normalization."""

from __future__ import annotations

from tokenpal.ui.ascii_skeletons import SKELETONS, _SAMPLE_PALETTES, render
from tokenpal.ui.ascii_zones import (
    BODY_MOTIF_OPTIONS,
    BODY_MOTIF_OVERLAYS,
    BODY_MOTIF_RUBRIC,
    EYE_REGION_OPTIONS,
    EYE_REGION_OVERLAYS,
    EYE_REGION_RUBRIC,
    FACIAL_HAIR_OPTIONS,
    FACIAL_HAIR_OVERLAYS,
    FACIAL_HAIR_RUBRIC,
    HEADWEAR_OPTIONS,
    HEADWEAR_OVERLAYS,
    HEADWEAR_RUBRIC,
    TRAILING_OPTIONS,
    TRAILING_OVERLAYS,
    TRAILING_RUBRIC,
    _REPLACE_TARGETS,
    _ZONE_COMPAT,
    _ZONE_MODES,
    apply_replace_zones,
    headwear_prefix,
    normalize_zones,
    trailing_suffix,
)


def test_every_headwear_option_has_overlay_and_rubric() -> None:
    for option in HEADWEAR_OPTIONS:
        assert option in HEADWEAR_OVERLAYS
        assert option in HEADWEAR_RUBRIC


def test_none_is_fronted_in_options() -> None:
    assert HEADWEAR_OPTIONS[0] == "none"


def test_every_skeleton_has_zone_compat_entry() -> None:
    compat = _ZONE_COMPAT["headwear"]
    for skeleton in SKELETONS:
        assert skeleton in compat, f"{skeleton} missing from headwear compat"
        allowed = compat[skeleton]
        assert "none" in allowed, f"{skeleton} must allow 'none'"
        for option in allowed:
            assert option in HEADWEAR_OPTIONS


def test_normalize_zones_coerces_illegal_to_none() -> None:
    out = normalize_zones("ghost_floating", {"headwear": "wizard_hat"})
    assert out["headwear"] == "none"
    out = normalize_zones("robot_boxy", {"headwear": "hood_with_ears"})
    assert out["headwear"] == "none"


def test_normalize_zones_preserves_legal_picks() -> None:
    out = normalize_zones("humanoid_tall", {"headwear": "hood_with_ears"})
    assert out["headwear"] == "hood_with_ears"
    out = normalize_zones("mystical_cloaked", {"headwear": "wizard_hat"})
    assert out["headwear"] == "wizard_hat"


def test_normalize_zones_fills_missing_keys_with_none() -> None:
    out = normalize_zones("humanoid_tall", {})
    assert out["headwear"] == "none"
    assert out["facial_hair"] == "none"


def test_normalize_zones_ignores_unknown_zone_keys() -> None:
    out = normalize_zones("humanoid_tall", {"fictional_zone": "anything"})
    assert "fictional_zone" not in out
    assert set(out.keys()) == {
        "headwear", "facial_hair", "body_motif", "eye_region", "trailing",
    }


def test_headwear_prefix_none_returns_empty_list() -> None:
    slots = {"c": "[/]", **_SAMPLE_PALETTES["humanoid_tall"]}
    assert headwear_prefix("none", slots) == []


def test_headwear_prefix_returns_rendered_rows() -> None:
    slots = {"c": "[/]", **_SAMPLE_PALETTES["humanoid_tall"]}
    rows = headwear_prefix("hood_with_ears", slots)
    assert len(rows) == 2
    assert "██" in rows[1]


def test_every_legal_skeleton_zone_combo_renders_without_exception() -> None:
    for skeleton in SKELETONS:
        palette = _SAMPLE_PALETTES[skeleton]
        for option in _ZONE_COMPAT["headwear"][skeleton]:
            frames = render(skeleton, palette, {"headwear": option})
            assert frames, f"{skeleton}+{option} rendered no frames"
            for line in frames:
                assert isinstance(line, str)


def test_render_without_zones_matches_no_headwear() -> None:
    palette = _SAMPLE_PALETTES["humanoid_tall"]
    plain = render("humanoid_tall", palette)
    with_none = render("humanoid_tall", palette, {"headwear": "none"})
    assert plain == with_none


def test_render_headwear_adds_rows_above_skeleton() -> None:
    palette = _SAMPLE_PALETTES["humanoid_tall"]
    plain = render("humanoid_tall", palette)
    with_hood = render("humanoid_tall", palette, {"headwear": "hood_with_ears"})
    assert len(with_hood) == len(plain) + 2


# ---------------------------------------------------------------
# facial_hair replace-mode zone
# ---------------------------------------------------------------


def test_every_facial_hair_option_has_overlay_and_rubric() -> None:
    for option in FACIAL_HAIR_OPTIONS:
        assert option in FACIAL_HAIR_OVERLAYS
        assert option in FACIAL_HAIR_RUBRIC


def test_facial_hair_zone_is_registered_as_replace_mode() -> None:
    assert _ZONE_MODES["facial_hair"] == "replace"


def test_every_replace_target_points_to_a_real_skeleton_zone_combo() -> None:
    from tokenpal.ui.ascii_zones import _REPLACE_OVERLAYS

    for (zone_name, option, skeleton), rows in _REPLACE_TARGETS.items():
        assert skeleton in SKELETONS
        assert _ZONE_MODES.get(zone_name) == "replace"
        assert option in _REPLACE_OVERLAYS[zone_name]
        assert option in _ZONE_COMPAT[zone_name][skeleton]
        start, end = rows
        assert 0 <= start < end <= 14


def test_beard_long_replaces_torso_rows_on_mystical_cloaked() -> None:
    palette = _SAMPLE_PALETTES["mystical_cloaked"]
    plain = render("mystical_cloaked", palette)
    with_beard = render(
        "mystical_cloaked", palette,
        {"headwear": "none", "facial_hair": "beard_long"},
    )
    assert len(with_beard) == len(plain)
    assert plain[:8] == with_beard[:8]
    assert plain[11:] == with_beard[11:]
    assert plain[8:11] != with_beard[8:11]


def test_beard_stubble_replaces_one_row_only() -> None:
    palette = _SAMPLE_PALETTES["humanoid_tall"]
    plain = render("humanoid_tall", palette)
    with_stubble = render(
        "humanoid_tall", palette,
        {"headwear": "none", "facial_hair": "beard_stubble"},
    )
    assert len(with_stubble) == len(plain)
    assert plain[7] != with_stubble[7]
    # Body below chin must survive — stubble only touches row 7.
    assert plain[8:] == with_stubble[8:]


def test_facial_hair_coerces_to_none_on_unsupported_skeleton() -> None:
    palette = _SAMPLE_PALETTES["robot_boxy"]
    plain = render("robot_boxy", palette)
    with_illegal = render(
        "robot_boxy", palette,
        {"headwear": "none", "facial_hair": "beard_long"},
    )
    assert plain == with_illegal


def test_apply_replace_zones_is_noop_when_all_none() -> None:
    palette = _SAMPLE_PALETTES["humanoid_tall"]
    slots = {"c": "[/]", **palette}
    body = SKELETONS["humanoid_tall"].format(**slots).splitlines()
    unchanged = apply_replace_zones(
        body, "humanoid_tall",
        {"headwear": "none", "facial_hair": "none", "body_motif": "none"},
        slots,
    )
    assert unchanged == body


# ---------------------------------------------------------------
# body_motif replace-mode zone
# ---------------------------------------------------------------


def test_every_body_motif_option_has_overlay_and_rubric() -> None:
    for option in BODY_MOTIF_OPTIONS:
        assert option in BODY_MOTIF_OVERLAYS
        assert option in BODY_MOTIF_RUBRIC


def test_body_motif_zone_is_registered_as_replace_mode() -> None:
    assert _ZONE_MODES["body_motif"] == "replace"


def test_screen_dpad_replaces_mid_torso_on_robot_boxy() -> None:
    palette = _SAMPLE_PALETTES["robot_boxy"]
    plain = render("robot_boxy", palette)
    with_screen = render(
        "robot_boxy", palette,
        {"headwear": "none", "facial_hair": "none", "body_motif": "screen_dpad"},
    )
    assert len(with_screen) == len(plain)
    assert plain[:10] == with_screen[:10]
    assert plain[12:] == with_screen[12:]
    assert plain[10:12] != with_screen[10:12]


def test_chest_door_differs_from_screen_dpad() -> None:
    palette = _SAMPLE_PALETTES["robot_boxy"]
    screen = render(
        "robot_boxy", palette,
        {"body_motif": "screen_dpad"},
    )
    door = render("robot_boxy", palette, {"body_motif": "chest_door"})
    assert screen[10:12] != door[10:12]


def test_body_motif_coerces_to_none_on_unsupported_skeleton() -> None:
    palette = _SAMPLE_PALETTES["humanoid_tall"]
    plain = render("humanoid_tall", palette)
    with_illegal = render(
        "humanoid_tall", palette, {"body_motif": "screen_dpad"},
    )
    assert plain == with_illegal


# ---------------------------------------------------------------
# eye_region replace-mode zone
# ---------------------------------------------------------------


def test_every_eye_region_option_has_overlay_and_rubric() -> None:
    for option in EYE_REGION_OPTIONS:
        assert option in EYE_REGION_OVERLAYS
        assert option in EYE_REGION_RUBRIC


def test_eye_region_zone_is_registered_as_replace_mode() -> None:
    assert _ZONE_MODES["eye_region"] == "replace"


def test_single_cyclops_replaces_eye_row_on_humanoid_tall() -> None:
    palette = _SAMPLE_PALETTES["humanoid_tall"]
    plain = render("humanoid_tall", palette)
    with_cyclops = render(
        "humanoid_tall", palette, {"eye_region": "single_cyclops"},
    )
    assert len(with_cyclops) == len(plain)
    assert plain[:4] == with_cyclops[:4]
    assert plain[5:] == with_cyclops[5:]
    assert plain[4] != with_cyclops[4]


def test_oversized_spiral_on_animal_quadruped_has_spiral_glyphs() -> None:
    palette = _SAMPLE_PALETTES["animal_quadruped"]
    with_spiral = render(
        "animal_quadruped", palette, {"eye_region": "oversized_spiral"},
    )
    eye_row = with_spiral[5]
    assert "◉" in eye_row
    assert "◎" in eye_row


def test_eye_region_coerces_to_none_on_unsupported_skeleton() -> None:
    palette = _SAMPLE_PALETTES["robot_boxy"]
    plain = render("robot_boxy", palette)
    with_illegal = render(
        "robot_boxy", palette, {"eye_region": "single_cyclops"},
    )
    assert plain == with_illegal


# ---------------------------------------------------------------
# trailing append-mode zone
# ---------------------------------------------------------------


def test_every_trailing_option_has_overlay_and_rubric() -> None:
    for option in TRAILING_OPTIONS:
        assert option in TRAILING_OVERLAYS
        assert option in TRAILING_RUBRIC


def test_trailing_zone_is_registered_as_append_mode() -> None:
    assert _ZONE_MODES["trailing"] == "append"


def test_trailing_suffix_none_returns_empty_list() -> None:
    slots = {"c": "[/]", **_SAMPLE_PALETTES["animal_quadruped"]}
    assert trailing_suffix("none", "animal_quadruped", slots) == []


def test_tail_curly_adds_rows_below_animal_quadruped() -> None:
    palette = _SAMPLE_PALETTES["animal_quadruped"]
    plain = render("animal_quadruped", palette)
    with_tail = render(
        "animal_quadruped", palette, {"trailing": "tail_curly"},
    )
    assert len(with_tail) == len(plain) + 1
    assert plain == with_tail[:len(plain)]


def test_hair_drift_adds_rows_below_ghost_floating() -> None:
    palette = _SAMPLE_PALETTES["ghost_floating"]
    plain = render("ghost_floating", palette)
    with_drift = render(
        "ghost_floating", palette, {"trailing": "hair_drift"},
    )
    assert len(with_drift) == len(plain) + 1


def test_trailing_coerces_to_none_on_unsupported_skeleton() -> None:
    palette = _SAMPLE_PALETTES["humanoid_tall"]
    plain = render("humanoid_tall", palette)
    with_illegal = render(
        "humanoid_tall", palette, {"trailing": "tail_curly"},
    )
    assert plain == with_illegal
