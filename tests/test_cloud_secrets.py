"""Tests for tokenpal/config/secrets.py — Anthropic API key storage."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from tokenpal.config.secrets import (
    clear_cloud_key,
    fingerprint,
    get_cloud_key,
    set_cloud_key,
)


@pytest.fixture()
def secrets_path(tmp_path: Path) -> Path:
    return tmp_path / ".secrets.json"


def test_get_missing_returns_none(secrets_path: Path) -> None:
    assert get_cloud_key(secrets_path) is None


def test_set_and_get_roundtrip(secrets_path: Path) -> None:
    key = "sk-ant-api03-" + "a" * 40
    set_cloud_key(key, secrets_path)
    assert get_cloud_key(secrets_path) == key


def test_set_cloud_key_chmods_0o600(secrets_path: Path) -> None:
    set_cloud_key("sk-ant-api03-" + "x" * 40, secrets_path)
    mode = stat.S_IMODE(os.stat(secrets_path).st_mode)
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


def test_set_rejects_non_anthropic_shape(secrets_path: Path) -> None:
    with pytest.raises(ValueError, match="sk-ant-"):
        set_cloud_key("not-a-real-key", secrets_path)
    assert get_cloud_key(secrets_path) is None


def test_set_rejects_openai_shape(secrets_path: Path) -> None:
    with pytest.raises(ValueError):
        set_cloud_key("sk-proj-abc123def456", secrets_path)


def test_set_rejects_empty_after_prefix(secrets_path: Path) -> None:
    with pytest.raises(ValueError):
        set_cloud_key("sk-ant-", secrets_path)


def test_clear_removes_field(secrets_path: Path) -> None:
    set_cloud_key("sk-ant-api03-" + "b" * 40, secrets_path)
    clear_cloud_key(secrets_path)
    assert get_cloud_key(secrets_path) is None
    # File still exists but empty dict is fine
    if secrets_path.exists():
        assert "cloud_key" not in json.loads(secrets_path.read_text())


def test_clear_noop_when_missing(secrets_path: Path) -> None:
    clear_cloud_key(secrets_path)  # should not raise


def test_fingerprint_shows_last_four() -> None:
    assert fingerprint("sk-ant-api03-abcdefgh1234") == "sk-ant-...1234"


def test_fingerprint_empty_key() -> None:
    assert fingerprint("") == "(none)"
    assert fingerprint("   ") == "(none)"


def test_set_preserves_other_secrets(secrets_path: Path) -> None:
    # Future-proofing: if someone adds other fields to .secrets.json, writing
    # the cloud key shouldn't wipe them.
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    secrets_path.write_text(json.dumps({"other_field": "keep-me"}))
    set_cloud_key("sk-ant-api03-" + "c" * 40, secrets_path)
    data = json.loads(secrets_path.read_text())
    assert data["other_field"] == "keep-me"
    assert data["cloud_key"].startswith("sk-ant-")
