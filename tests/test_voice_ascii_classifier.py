"""Tests for the v1b voice-training ASCII classifier.

Covers the new v1b surface: visual-tells grounding in the prompt,
headwear zone coercion, highlight palette fallback, cloud-gate
short-circuit, and hue-bucket assertions for golden palettes. Base
JSON-parse + skeleton-render coverage lives in
``tests/test_tools/test_ascii_skeletons.py``.
"""

from __future__ import annotations

import json

import pytest

from tokenpal.tools import train_voice
from tokenpal.tools.train_voice import (
    _build_classifier_prompt,
    _classify_character_for_skeleton,
    _classify_via_cloud,
    _DEFAULT_CLASSIFICATION,
    _parse_classification_json,
    _render_skeleton_frames,
)
from tokenpal.tools.voice_profile import attach_visual_tells
from tokenpal.util.color import hex_to_hue_bucket


def _good_json(**overrides: object) -> str:
    data = {
        "skeleton": "humanoid_tall",
        "palette": {
            "hair": "#ffffff", "skin": "#f4d4a8", "outfit": "#3da8e8",
            "accent": "#ffd700", "shadow": "#2a6fa5", "highlight": "#66ccff",
        },
        "eye": "●",
        "mouth": "▽",
        "zones": {"headwear": "none"},
    }
    data.update(overrides)
    return json.dumps(data)


# --- v1b JSON parsing (new surface only) ----------------------------------


def test_parse_accepts_full_v1b_json() -> None:
    parsed = _parse_classification_json(_good_json())
    assert parsed is not None
    assert parsed["palette"]["highlight"] == "#66ccff"
    assert parsed["zones"]["headwear"] == "none"
    assert parsed["zones"]["facial_hair"] == "none"


def test_parse_falls_back_highlight_to_outfit_when_missing() -> None:
    legacy = json.dumps({
        "skeleton": "humanoid_tall",
        "palette": {
            "hair": "#ffffff", "skin": "#f4d4a8", "outfit": "#3da8e8",
            "accent": "#ffd700", "shadow": "#2a6fa5",
        },
        "eye": "●", "mouth": "▽",
    })
    parsed = _parse_classification_json(legacy)
    assert parsed is not None
    assert parsed["palette"]["highlight"] == "#3da8e8"
    assert parsed["zones"]["headwear"] == "none"
    assert parsed["zones"]["facial_hair"] == "none"


def test_parse_coerces_illegal_zone_combo() -> None:
    raw = _good_json(
        skeleton="ghost_floating",
        zones={"headwear": "wizard_hat", "facial_hair": "beard_long"},
    )
    parsed = _parse_classification_json(raw)
    assert parsed is not None
    assert parsed["zones"]["headwear"] == "none"
    assert parsed["zones"]["facial_hair"] == "none"


# --- Prompt assembly ------------------------------------------------------


def test_build_classifier_prompt_includes_visual_canon_when_available() -> None:
    persona = attach_visual_tells(
        "VOICE: x", "white bear-ear hood, cyan tee, navy shorts",
    )
    prompt = _build_classifier_prompt("Finn", persona, "adventuretime.fandom.com")
    assert "Visual canon" in prompt
    assert "bear-ear hood" in prompt


def test_build_classifier_prompt_falls_back_when_no_visual() -> None:
    prompt = _build_classifier_prompt("test", "VOICE: x", "")
    assert "Visual canon" not in prompt
    assert "bright, terminal-readable colors" in prompt


def test_build_classifier_prompt_lists_all_headwear_options() -> None:
    from tokenpal.ui.ascii_zones import HEADWEAR_RUBRIC

    prompt = _build_classifier_prompt("test", "VOICE: x", "")
    for option in HEADWEAR_RUBRIC:
        assert f"- {option}:" in prompt


# --- Local classifier (stubbed LLM) ---------------------------------------


def test_local_classifier_returns_parsed_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(train_voice, "_ollama_generate", lambda *a, **kw: _good_json())
    result = _classify_character_for_skeleton("test", "VOICE: x", "")
    assert result is not None
    assert result["skeleton"] == "humanoid_tall"


def test_local_classifier_retries_on_bad_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(["garbage not json", _good_json()])
    monkeypatch.setattr(
        train_voice, "_ollama_generate",
        lambda *a, **kw: next(responses),
    )
    result = _classify_character_for_skeleton("test", "VOICE: x", "")
    assert result is not None


def test_local_classifier_returns_none_on_persistent_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(train_voice, "_ollama_generate", lambda *a, **kw: "nope")
    assert _classify_character_for_skeleton("test", "VOICE: x", "") is None


# --- Cloud classifier gate ------------------------------------------------


def test_cloud_classifier_returns_none_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import dataclass

    from tokenpal.config import loader
    from tokenpal.config.schema import CloudLLMConfig

    @dataclass
    class _Cfg:
        cloud_llm: CloudLLMConfig

    cfg = _Cfg(cloud_llm=CloudLLMConfig(enabled=True, voice_classifier=False))
    monkeypatch.setattr(loader, "load_config", lambda: cfg)
    assert _classify_via_cloud("test", "", "") is None


# --- Golden hue buckets ---------------------------------------------------


@pytest.mark.parametrize(
    "skeleton, palette_overrides, visual_tells, expected",
    [
        # Finn with canonical VISUAL — white hood, cyan shirt, navy shorts
        (
            "humanoid_tall",
            {
                "hair": "#ffffff", "skin": "#ffdab9", "outfit": "#00ffff",
                "accent": "#000080",
            },
            "white bear-ear hood, pale peach skin, cyan tee, navy shorts",
            {"hair": "white", "outfit": "cyan", "accent": "blue"},
        ),
        # Bender with canonical VISUAL — silver-gray metal
        (
            "robot_boxy",
            {
                "hair": "#c0c0c0", "skin": "#c0c0c0", "outfit": "#c0c0c0",
                "accent": "#ffff00",
            },
            "shiny silver-gray metal, yellow eyes, antenna",
            {"hair": "gray", "outfit": "gray", "accent": "yellow"},
        ),
    ],
)
def test_hue_buckets_match_canonical(
    skeleton: str, palette_overrides: dict, visual_tells: str,
    expected: dict,
) -> None:
    for key, bucket in expected.items():
        assert hex_to_hue_bucket(palette_overrides[key]) == bucket, (
            f"{skeleton}:{key} expected {bucket}, "
            f"got {hex_to_hue_bucket(palette_overrides[key])}"
        )


def testhex_to_hue_bucket_spot_checks() -> None:
    assert hex_to_hue_bucket("#ffffff") == "white"
    assert hex_to_hue_bucket("#000000") == "black"
    assert hex_to_hue_bucket("#808080") == "gray"
    assert hex_to_hue_bucket("#ff0000") == "red"
    assert hex_to_hue_bucket("#00ff00") == "green"
    assert hex_to_hue_bucket("#0000ff") == "blue"
    assert hex_to_hue_bucket("#00ffff") == "cyan"
    assert hex_to_hue_bucket("#ffff00") == "yellow"
    assert hex_to_hue_bucket("#ff8800") == "orange"
    assert hex_to_hue_bucket("#8800ff") == "purple"


# --- Render pipeline -------------------------------------------------------


def test_render_default_classification_produces_three_frames() -> None:
    frames = _render_skeleton_frames(_DEFAULT_CLASSIFICATION)
    assert len(frames) == 3
    idle, idle_alt, talking = frames
    assert len(idle) == len(idle_alt) == len(talking)


def test_render_with_headwear_prefixes_rows() -> None:
    cls = dict(_DEFAULT_CLASSIFICATION)
    cls["zones"] = {"headwear": "crown"}
    frames = _render_skeleton_frames(cls)
    baseline = _render_skeleton_frames(_DEFAULT_CLASSIFICATION)
    assert len(frames[0]) > len(baseline[0])


# --- Mood-aware frame generation ------------------------------------------


def test_render_mood_frames_returns_empty_when_no_roles() -> None:
    from tokenpal.tools.train_voice import _render_mood_frames

    out = _render_mood_frames(_DEFAULT_CLASSIFICATION, {})
    assert out == {}


def test_render_mood_frames_emits_triple_per_known_role() -> None:
    from tokenpal.tools.train_voice import _render_mood_frames

    mood_roles = {
        "sleepy": "DROWSY",
        "bored": "MEH",
        "hyper": "AMPED",
        "default": "CHILL",
    }
    out = _render_mood_frames(_DEFAULT_CLASSIFICATION, mood_roles)
    assert set(out.keys()) == {"sleepy", "bored", "hyper"}
    for triple in out.values():
        assert set(triple.keys()) == {"idle", "idle_alt", "talking"}
        assert len(triple["idle"]) == len(triple["idle_alt"]) == len(
            triple["talking"]
        )


def test_render_mood_frames_sleepy_differs_from_default_idle() -> None:
    from tokenpal.tools.train_voice import _render_mood_frames

    mood_roles = {"sleepy": "DROWSY"}
    mood = _render_mood_frames(_DEFAULT_CLASSIFICATION, mood_roles)
    default_idle, _, _ = _render_skeleton_frames(_DEFAULT_CLASSIFICATION)
    assert mood["sleepy"]["idle"] != default_idle
