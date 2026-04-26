"""Tests for tokenpal/config/secrets.py — Anthropic API key storage."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from tokenpal.config.secrets import (
    clear_cloud_key,
    clear_tavily_key,
    fingerprint,
    get_brave_key,
    get_cloud_key,
    get_tavily_key,
    load_search_keys,
    set_brave_key,
    set_cloud_key,
    set_tavily_key,
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
    assert data["anthropic_key"].startswith("sk-ant-")


# ---- Legacy migration ------------------------------------------------------


def test_legacy_cloud_key_field_is_readable(secrets_path: Path) -> None:
    """Users with a pre-migration .secrets.json written under the old
    `cloud_key` field must keep working after the upgrade."""
    legacy_key = "sk-ant-api03-" + "l" * 40
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    secrets_path.write_text(json.dumps({"cloud_key": legacy_key}))
    assert get_cloud_key(secrets_path) == legacy_key


def test_legacy_field_migrates_on_next_write(secrets_path: Path) -> None:
    """On the first write after read, the file is rewritten in the new
    shape — the `cloud_key` field is gone, `anthropic_key` is present."""
    legacy_key = "sk-ant-api03-" + "m" * 40
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    secrets_path.write_text(json.dumps({"cloud_key": legacy_key}))

    # Touch the file via any write-path call.
    set_tavily_key("tvly-" + "n" * 32, secrets_path)

    data = json.loads(secrets_path.read_text())
    assert "cloud_key" not in data
    assert data["anthropic_key"] == legacy_key
    assert data["tavily_key"].startswith("tvly-")


def test_legacy_read_wins_when_both_fields_present(secrets_path: Path) -> None:
    """If both legacy and new fields exist, the new field wins — so an
    explicit /cloud anthropic enable overrides a stale legacy entry."""
    new_key = "sk-ant-api03-" + "p" * 40
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    secrets_path.write_text(json.dumps({
        "cloud_key": "sk-ant-api03-" + "q" * 40,
        "anthropic_key": new_key,
    }))
    assert get_cloud_key(secrets_path) == new_key


# ---- Multi-key coexistence -------------------------------------------------


def test_anthropic_tavily_brave_coexist(secrets_path: Path) -> None:
    ak = "sk-ant-api03-" + "a" * 40
    tk = "tvly-" + "b" * 32
    bk = "BSA_" + "c" * 28
    set_cloud_key(ak, secrets_path)
    set_tavily_key(tk, secrets_path)
    set_brave_key(bk, secrets_path)

    assert get_cloud_key(secrets_path) == ak
    assert get_tavily_key(secrets_path) == tk
    assert get_brave_key(secrets_path) == bk

    data = json.loads(secrets_path.read_text())
    assert data.keys() == {"anthropic_key", "tavily_key", "brave_key"}


def test_clear_only_removes_target_field(secrets_path: Path) -> None:
    ak = "sk-ant-api03-" + "d" * 40
    tk = "tvly-" + "e" * 32
    set_cloud_key(ak, secrets_path)
    set_tavily_key(tk, secrets_path)

    clear_tavily_key(secrets_path)
    assert get_cloud_key(secrets_path) == ak
    assert get_tavily_key(secrets_path) is None


def test_clear_cloud_key_also_wipes_legacy(secrets_path: Path) -> None:
    """/cloud anthropic forget should leave the file clean of the legacy
    field too, not let it resurrect on the next read."""
    legacy_key = "sk-ant-api03-" + "f" * 40
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    secrets_path.write_text(json.dumps({"cloud_key": legacy_key}))

    clear_cloud_key(secrets_path)
    data = json.loads(secrets_path.read_text()) if secrets_path.exists() else {}
    assert "cloud_key" not in data
    assert "anthropic_key" not in data


# ---- Tavily / Brave validators --------------------------------------------


def test_set_tavily_rejects_non_tavily_shape(secrets_path: Path) -> None:
    with pytest.raises(ValueError, match="tvly-"):
        set_tavily_key("not-a-tavily-key", secrets_path)
    assert get_tavily_key(secrets_path) is None


def test_set_brave_rejects_too_short(secrets_path: Path) -> None:
    with pytest.raises(ValueError):
        set_brave_key("short", secrets_path)
    assert get_brave_key(secrets_path) is None


# ---- Fingerprint extensions ------------------------------------------------


def test_fingerprint_preserves_tavily_prefix() -> None:
    assert fingerprint("tvly-abcdefgh1234") == "tvly-...1234"


def test_fingerprint_opaque_key_has_no_prefix() -> None:
    # Brave keys have no stable prefix — fall back to generic redaction.
    assert fingerprint("BSA_randomopaquekey1234") == "...1234"


def test_load_search_keys_empty_when_unset(secrets_path: Path) -> None:
    assert load_search_keys(True, secrets_path) == {}
    assert load_search_keys(False, secrets_path) == {}


def test_load_search_keys_tavily_gated_on_cloud_search(secrets_path: Path) -> None:
    set_tavily_key("tvly-" + "a" * 32, secrets_path)
    assert load_search_keys(False, secrets_path) == {}
    assert load_search_keys(True, secrets_path) == {"tavily": "tvly-" + "a" * 32}


def test_load_search_keys_brave_not_gated(secrets_path: Path) -> None:
    # Brave is "presence = active" per the cloud-modal design; cloud_search
    # enabled/disabled must not influence whether the key is returned.
    brave_key = "B" * 32
    set_brave_key(brave_key, secrets_path)
    assert load_search_keys(False, secrets_path) == {"brave": brave_key}
    assert load_search_keys(True, secrets_path) == {"brave": brave_key}


def test_load_search_keys_both_keys_present(secrets_path: Path) -> None:
    set_tavily_key("tvly-" + "a" * 32, secrets_path)
    set_brave_key("B" * 32, secrets_path)
    keys = load_search_keys(True, secrets_path)
    assert keys == {"tavily": "tvly-" + "a" * 32, "brave": "B" * 32}
