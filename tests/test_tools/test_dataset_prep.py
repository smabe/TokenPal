"""Tests for the dataset preparation pipeline."""

from __future__ import annotations

import json
from pathlib import Path

from tokenpal.tools.dataset_prep import (
    DatasetConfig,
    build_system_prompt,
    prepare_dataset,
    voice_to_conversations,
)
from tokenpal.tools.voice_profile import VoiceProfile


def _make_profile(n_lines: int = 100) -> VoiceProfile:
    return VoiceProfile(
        character="Mordecai",
        source="regularshow.fandom.com",
        created="2026-01-01",
        lines=[f"Dude, that's line number {i}." for i in range(n_lines)],
        persona="A laid-back blue jay who says 'dude' a lot.",
    )


def test_build_system_prompt_includes_character():
    profile = _make_profile()
    prompt = build_system_prompt(profile)
    assert "Mordecai" in prompt


def test_build_system_prompt_includes_persona():
    profile = _make_profile()
    prompt = build_system_prompt(profile)
    assert "blue jay" in prompt


def test_build_system_prompt_includes_rules():
    profile = _make_profile()
    prompt = build_system_prompt(profile)
    assert "[SILENT]" in prompt
    assert "1-2 sentences" in prompt


def test_voice_to_conversations_format():
    profile = _make_profile(50)
    convos = voice_to_conversations(profile)
    assert len(convos) > 0
    for item in convos:
        assert "conversations" in item
        turns = item["conversations"]
        assert len(turns) == 3
        assert turns[0]["from"] == "system"
        assert turns[1]["from"] == "human"
        assert turns[2]["from"] == "gpt"


def test_voice_to_conversations_count():
    profile = _make_profile(50)
    convos = voice_to_conversations(profile)
    # Should produce one conversation per valid line
    assert len(convos) == 50


def test_voice_to_conversations_gpt_is_voice_line():
    profile = _make_profile(20)
    convos = voice_to_conversations(profile)
    gpt_responses = {item["conversations"][2]["value"] for item in convos}
    # All GPT responses should be voice lines
    for resp in gpt_responses:
        assert resp.startswith("Dude, that's line number")


def test_system_prompt_in_every_conversation():
    profile = _make_profile(20)
    convos = voice_to_conversations(profile)
    for item in convos:
        sys_msg = item["conversations"][0]["value"]
        assert "Mordecai" in sys_msg


def test_min_line_filtering():
    profile = VoiceProfile(
        character="Test",
        source="test",
        created="2026-01-01",
        lines=["Hi", "Short", "This line is long enough to pass the filter."],
        persona="Test persona.",
    )
    convos = voice_to_conversations(profile)
    # Only the long line should pass (min_line_length=15)
    assert len(convos) == 1
    assert "long enough" in convos[0]["conversations"][2]["value"]


def test_empty_profile_returns_empty():
    profile = VoiceProfile(
        character="Test",
        source="test",
        created="2026-01-01",
        lines=[],
        persona="Test.",
    )
    convos = voice_to_conversations(profile)
    assert convos == []


def test_user_prompt_variety():
    profile = _make_profile(100)
    convos = voice_to_conversations(profile)
    human_msgs = {item["conversations"][1]["value"] for item in convos}
    # Should have more than a few unique human messages
    assert len(human_msgs) > 10


def test_conversation_types_present():
    """Verify that observation, conversation, and freeform types all appear."""
    profile = _make_profile(100)
    convos = voice_to_conversations(profile)
    human_msgs = [item["conversations"][1]["value"] for item in convos]

    has_observation = any("What you see right now:" in m for m in human_msgs)
    has_conversation = any('User says:' in m for m in human_msgs)
    has_freeform = any(
        "random thought" in m.lower() or "in character" in m.lower()
        for m in human_msgs
    )
    assert has_observation
    assert has_conversation
    assert has_freeform


def test_prepare_dataset_writes_files(tmp_path: Path):
    profile = _make_profile(50)
    train_path, val_path = prepare_dataset(profile, tmp_path)

    assert train_path.exists()
    assert val_path.exists()
    assert train_path.name == "train.jsonl"
    assert val_path.name == "val.jsonl"


def test_prepare_dataset_split_ratio(tmp_path: Path):
    profile = _make_profile(100)
    config = DatasetConfig(train_ratio=0.8)
    train_path, val_path = prepare_dataset(profile, tmp_path, config)

    train_count = sum(1 for _ in train_path.open())
    val_count = sum(1 for _ in val_path.open())

    assert train_count == 80
    assert val_count == 20


def test_prepare_dataset_valid_jsonl(tmp_path: Path):
    profile = _make_profile(30)
    train_path, _ = prepare_dataset(profile, tmp_path)

    for line in train_path.open():
        data = json.loads(line)
        assert "conversations" in data


def test_prepare_dataset_raises_on_empty(tmp_path: Path):
    profile = VoiceProfile(
        character="Test",
        source="test",
        created="2026-01-01",
        lines=["Hi"],  # Too short to pass filter
        persona="Test.",
    )
    try:
        prepare_dataset(profile, tmp_path)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "No valid lines" in str(e)


def test_deterministic_with_seed(tmp_path: Path):
    profile = _make_profile(50)
    t1, _ = prepare_dataset(profile, tmp_path / "a")
    t2, _ = prepare_dataset(profile, tmp_path / "b")

    lines1 = t1.read_text()
    lines2 = t2.read_text()
    assert lines1 == lines2
