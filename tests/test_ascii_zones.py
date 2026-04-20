"""Unit tests for ascii_zones headwear overlay + compat normalization."""

from __future__ import annotations

from tokenpal.ui.ascii_skeletons import SKELETONS, _SAMPLE_PALETTES, render
from tokenpal.ui.ascii_zones import (
    HEADWEAR_OPTIONS,
    HEADWEAR_OVERLAYS,
    HEADWEAR_RUBRIC,
    _ZONE_COMPAT,
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
    assert normalize_zones("ghost_floating", {"headwear": "wizard_hat"}) == {
        "headwear": "none",
    }
    assert normalize_zones("robot_boxy", {"headwear": "hood_with_ears"}) == {
        "headwear": "none",
    }


def test_normalize_zones_preserves_legal_picks() -> None:
    assert normalize_zones("humanoid_tall", {"headwear": "hood_with_ears"}) == {
        "headwear": "hood_with_ears",
    }
    assert normalize_zones("mystical_cloaked", {"headwear": "wizard_hat"}) == {
        "headwear": "wizard_hat",
    }


def test_normalize_zones_fills_missing_keys_with_none() -> None:
    assert normalize_zones("humanoid_tall", {}) == {"headwear": "none"}


def test_normalize_zones_ignores_unknown_zone_keys() -> None:
    out = normalize_zones("humanoid_tall", {"fictional_zone": "anything"})
    assert "fictional_zone" not in out
    assert out == {"headwear": "none"}


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
