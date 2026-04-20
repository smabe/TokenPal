"""Unit tests for ascii_zones headwear overlay + compat normalization."""

from __future__ import annotations

from tokenpal.ui.ascii_skeletons import SKELETONS, _SAMPLE_PALETTES, render
from tokenpal.ui.ascii_zones import (
    FACIAL_HAIR_OPTIONS,
    FACIAL_HAIR_OVERLAYS,
    FACIAL_HAIR_RUBRIC,
    HEADWEAR_OPTIONS,
    HEADWEAR_OVERLAYS,
    HEADWEAR_RUBRIC,
    _REPLACE_TARGETS,
    _ZONE_COMPAT,
    _ZONE_MODES,
    apply_replace_zones,
    headwear_prefix,
    normalize_zones,
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
    assert set(out.keys()) == {"headwear", "facial_hair"}


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
    for (zone_name, option, skeleton), rows in _REPLACE_TARGETS.items():
        assert skeleton in SKELETONS
        assert _ZONE_MODES.get(zone_name) == "replace"
        assert option in FACIAL_HAIR_OPTIONS
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
        {"headwear": "none", "facial_hair": "none"}, slots,
    )
    assert unchanged == body
