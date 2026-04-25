"""Tests for tokenpal/config/consent.py — per-category consent storage."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from tokenpal.config.consent import (
    ALL_CATEGORIES,
    Category,
    has_consent,
    load_consent,
    save_consent,
)


@pytest.fixture()
def consent_path(tmp_path: Path) -> Path:
    return tmp_path / "consent.json"


def test_load_consent_missing_file_returns_all_false(consent_path: Path) -> None:
    flags = load_consent(consent_path)
    assert flags == {c: False for c in ALL_CATEGORIES}


def test_save_and_load_roundtrip(consent_path: Path) -> None:
    save_consent({Category.WEB_FETCHES: True, Category.RESEARCH_MODE: True}, consent_path)
    flags = load_consent(consent_path)
    assert flags[Category.WEB_FETCHES] is True
    assert flags[Category.RESEARCH_MODE] is True
    assert flags[Category.LOCATION_LOOKUPS] is False


def test_save_consent_chmods_0o600(consent_path: Path) -> None:
    save_consent({Category.WEB_FETCHES: True}, consent_path)
    mode = stat.S_IMODE(os.stat(consent_path).st_mode)
    assert mode == 0o600


def test_save_consent_creates_parent(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "consent.json"
    save_consent({Category.WEB_FETCHES: True}, path)
    assert path.exists()


def test_save_consent_drops_unknown_keys(consent_path: Path) -> None:
    save_consent({"made_up": True, Category.WEB_FETCHES: True}, consent_path)
    raw = json.loads(consent_path.read_text())
    assert "made_up" not in raw
    assert raw[Category.WEB_FETCHES] is True


def test_load_consent_handles_corrupt_json(consent_path: Path) -> None:
    consent_path.write_text("{not json")
    flags = load_consent(consent_path)
    assert flags == {c: False for c in ALL_CATEGORIES}


def test_has_consent_returns_category_value(consent_path: Path) -> None:
    save_consent({Category.RESEARCH_MODE: True}, consent_path)
    assert has_consent(Category.RESEARCH_MODE, consent_path) is True
    assert has_consent(Category.WEB_FETCHES, consent_path) is False


def test_has_consent_rejects_unknown_category(consent_path: Path) -> None:
    save_consent({Category.WEB_FETCHES: True}, consent_path)
    assert has_consent("nonexistent", consent_path) is False


def test_audio_categories_registered() -> None:
    assert Category.AUDIO_INPUT in ALL_CATEGORIES
    assert Category.AUDIO_OUTPUT in ALL_CATEGORIES
