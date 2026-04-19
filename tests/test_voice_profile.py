"""Tests for voice_profile helpers — focused on the list_profile_summaries
pass that VoiceModal depends on."""

from __future__ import annotations

from pathlib import Path

from tokenpal.tools.voice_profile import (
    ProfileSummary,
    VoiceProfile,
    list_profile_summaries,
    save_profile,
)


def _make(character: str, lines: int, *, finetuned: str = "") -> VoiceProfile:
    return VoiceProfile(
        character=character,
        source="adventuretime.fandom.com",
        created="2026-01-01T00:00:00",
        lines=["x"] * lines,
        finetuned_model=finetuned,
    )


def test_list_profile_summaries_empty_dir_returns_empty_list(
    tmp_path: Path,
) -> None:
    assert list_profile_summaries(tmp_path / "nope") == []
    (tmp_path / "voices").mkdir()
    assert list_profile_summaries(tmp_path / "voices") == []


def test_list_profile_summaries_returns_metadata_in_one_pass(
    tmp_path: Path,
) -> None:
    voices = tmp_path / "voices"
    voices.mkdir()
    save_profile(_make("Finn", 12), voices)
    save_profile(_make("Jake", 34, finetuned="tokenpal-jake"), voices)

    results = list_profile_summaries(voices)

    assert len(results) == 2
    by_slug = {r.slug: r for r in results}

    assert by_slug["finn"] == ProfileSummary(
        slug="finn",
        character="Finn",
        line_count=12,
        source="adventuretime.fandom.com",
        finetuned_model="",
    )
    assert by_slug["jake"].finetuned_model == "tokenpal-jake"
    assert by_slug["jake"].line_count == 34


def test_list_profile_summaries_skips_malformed_json(
    tmp_path: Path,
) -> None:
    voices = tmp_path / "voices"
    voices.mkdir()
    save_profile(_make("Finn", 3), voices)
    (voices / "broken.json").write_text("{not json")
    (voices / "missing-key.json").write_text('{"source": "x"}')

    results = list_profile_summaries(voices)
    assert [r.slug for r in results] == ["finn"]


def test_profile_summary_is_frozen() -> None:
    s = ProfileSummary(
        slug="finn", character="Finn", line_count=1,
        source="", finetuned_model="",
    )
    import pytest
    with pytest.raises(AttributeError):
        s.slug = "other"  # type: ignore[misc]
