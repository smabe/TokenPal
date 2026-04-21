"""Tests for skeleton-based ASCII generation: render, JSON classification,
fallback on bad LLM output, and the glyph-flip for blink/talking."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from rich.text import Text

from tokenpal.tools import train_voice
from tokenpal.tools.train_voice import (
    _DEFAULT_CLASSIFICATION,
    _classify_character_for_skeleton,
    _generate_ascii_art,
    _parse_classification_json,
    _render_skeleton_frames,
)
from tokenpal.ui.ascii_skeletons import (
    _SAMPLE_PALETTES,
    CELL_WIDTH,
    PALETTE_KEYS,
    render,
)

# ---------------------------------------------------------------
# ascii_skeletons.render — every skeleton + sample palette combo
# ---------------------------------------------------------------


class TestSkeletonRender:
    @pytest.mark.parametrize("name", sorted(_SAMPLE_PALETTES))
    def test_each_skeleton_is_14_lines_by_cell_width(self, name: str) -> None:
        lines = render(name, _SAMPLE_PALETTES[name])
        assert len(lines) == 14
        for i, line in enumerate(lines, 1):
            cell_len = Text.from_markup(line).cell_len
            assert cell_len == CELL_WIDTH, (
                f"{name} row {i} is {cell_len} cells, expected {CELL_WIDTH}"
            )

    def test_missing_palette_key_raises(self) -> None:
        bad = dict(_SAMPLE_PALETTES["humanoid_tall"])
        bad.pop("skin")
        with pytest.raises(KeyError):
            render("humanoid_tall", bad)


# ---------------------------------------------------------------
# _parse_classification_json
# ---------------------------------------------------------------


def _good_json_payload() -> str:
    return json.dumps({
        "skeleton": "humanoid_tall",
        "palette": {
            "hair": "#ffcc44",
            "skin": "#f4d4a8",
            "outfit": "#3da8e8",
            "accent": "#ffd700",
            "shadow": "#2a6fa5",
            "highlight": "#66ccff",
        },
        "eye": "●",
        "mouth": "▽",
        "zones": {"headwear": "none"},
    })


class TestParseClassificationJSON:
    def test_happy_path(self) -> None:
        parsed = _parse_classification_json(_good_json_payload())
        assert parsed is not None
        assert parsed["skeleton"] == "humanoid_tall"
        assert set(parsed["palette"].keys()) == set(PALETTE_KEYS)

    def test_strips_prose_around_json(self) -> None:
        text = "Sure! Here is the JSON:\n" + _good_json_payload() + "\nLet me know."
        assert _parse_classification_json(text) is not None

    def test_strips_markdown_fences(self) -> None:
        text = "```json\n" + _good_json_payload() + "\n```"
        assert _parse_classification_json(text) is not None

    def test_rejects_unknown_skeleton(self) -> None:
        bad = json.loads(_good_json_payload())
        bad["skeleton"] = "cthulhu"
        assert _parse_classification_json(json.dumps(bad)) is None

    def test_rejects_missing_palette_key(self) -> None:
        bad = json.loads(_good_json_payload())
        bad["palette"].pop("shadow")
        assert _parse_classification_json(json.dumps(bad)) is None

    def test_rejects_malformed_hex(self) -> None:
        bad = json.loads(_good_json_payload())
        bad["palette"]["hair"] = "#zzzzzz"
        assert _parse_classification_json(json.dumps(bad)) is None

    def test_rejects_multichar_eye(self) -> None:
        bad = json.loads(_good_json_payload())
        bad["eye"] = "●●"
        assert _parse_classification_json(json.dumps(bad)) is None

    def test_rejects_eye_with_markup(self) -> None:
        # An opening bracket would sneak Rich markup into the skeleton.
        bad = json.loads(_good_json_payload())
        bad["eye"] = "["
        assert _parse_classification_json(json.dumps(bad)) is None

    def test_rejects_empty_input(self) -> None:
        assert _parse_classification_json("") is None
        assert _parse_classification_json("no json here") is None

    def test_rejects_broken_json(self) -> None:
        assert _parse_classification_json('{"skeleton": "humanoid_tall",') is None


# ---------------------------------------------------------------
# _render_skeleton_frames — glyph flip for blink + talking
# ---------------------------------------------------------------


class TestRenderSkeletonFrames:
    def test_three_frames_same_skeleton_differ_only_in_face(self) -> None:
        idle, idle_alt, talking = _render_skeleton_frames(_DEFAULT_CLASSIFICATION)
        assert len(idle) == len(idle_alt) == len(talking) == 14
        # idle_alt must differ (blink) but share the majority of rows with idle
        differing = sum(1 for a, b in zip(idle, idle_alt, strict=True) if a != b)
        assert 1 <= differing <= 3, f"blink changed {differing} rows, expected 1-3"
        # talking must differ from idle too (mouth flip)
        differing = sum(1 for a, b in zip(idle, talking, strict=True) if a != b)
        assert 1 <= differing <= 3

    def test_idle_alt_contains_blink_glyph(self) -> None:
        _, idle_alt, _ = _render_skeleton_frames(_DEFAULT_CLASSIFICATION)
        # At least one row must contain the blink glyph ─
        assert any("─" in row for row in idle_alt)

    def test_talking_flips_mouth_glyph(self) -> None:
        idle, _, talking = _render_skeleton_frames(_DEFAULT_CLASSIFICATION)
        # Default mouth ▽ should not appear in talking; ◇ should.
        assert not any("▽" in row for row in talking)
        assert any("◇" in row for row in talking)


# ---------------------------------------------------------------
# _generate_ascii_art — end-to-end with mocked LLM
# ---------------------------------------------------------------


class TestGenerateAsciiArt:
    def test_happy_path_returns_14_line_frames(self) -> None:
        with patch.object(
            train_voice, "_classify_via_cloud", return_value=None,
        ), patch.object(
            train_voice, "_classify_character_for_skeleton",
            return_value=_DEFAULT_CLASSIFICATION,
        ):
            idle, idle_alt, talking, _cls = _generate_ascii_art(
                "Finn", "persona text",
            )
        assert len(idle) == 14 and len(idle_alt) == 14 and len(talking) == 14

    def test_falls_back_to_default_when_classification_fails(self) -> None:
        # Simulate persistent LLM failure (returned None both retries).
        with patch.object(
            train_voice, "_classify_via_cloud", return_value=None,
        ), patch.object(
            train_voice, "_classify_character_for_skeleton", return_value=None,
        ):
            idle, idle_alt, talking, _cls = _generate_ascii_art(
                "Cthulhu", "unknowable",
            )
        # Frames still render — they use the default classification.
        assert len(idle) == 14
        # Default skeleton is humanoid_tall with default mouth ▽.
        assert any("▽" in row for row in idle)


# ---------------------------------------------------------------
# _classify_character_for_skeleton — retry + fallback semantics
# ---------------------------------------------------------------


class TestClassifyRetryLoop:
    def test_retries_once_at_lower_temp_on_first_failure(self) -> None:
        attempts: list[float] = []

        def fake_gen(prompt: str, max_tokens: int, temperature: float) -> str | None:
            attempts.append(temperature)
            # First call returns garbage; second call returns valid JSON.
            return None if len(attempts) == 1 else _good_json_payload()

        with patch.object(train_voice, "_ollama_generate", side_effect=fake_gen):
            result = _classify_character_for_skeleton("Finn", "persona")

        assert result is not None
        assert attempts == [0.5, 0.3]

    def test_returns_none_when_both_retries_fail(self) -> None:
        with patch.object(train_voice, "_ollama_generate", return_value=None):
            assert _classify_character_for_skeleton("Finn", "persona") is None
