"""Tests for tokenpal/config/ui_state.py: window visibility persistence."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from tokenpal.config.ui_state import load_ui_state, save_ui_state

_DEFAULTS = {"buddy_visible": True, "windows": {}, "zoom": 1.0}


def test_load_missing_returns_defaults(tmp_path: Path) -> None:
    state = load_ui_state(tmp_path)
    assert state == _DEFAULTS


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    save_ui_state(
        tmp_path,
        {
            "buddy_visible": False,
            "windows": {"chat": True, "news": True},
            "zoom": 1.5,
        },
    )
    assert load_ui_state(tmp_path) == {
        "buddy_visible": False,
        "windows": {"chat": True, "news": True},
        "zoom": 1.5,
    }


def test_arbitrary_window_names_persist(tmp_path: Path) -> None:
    """Adding a new toggleable window must NOT require a schema bump:
    the persistence layer accepts any registered name."""
    save_ui_state(
        tmp_path,
        {
            "buddy_visible": True,
            "windows": {"chat": True, "stats_dashboard": True},
            "zoom": 1.0,
        },
    )
    state = load_ui_state(tmp_path)
    assert state["windows"]["stats_dashboard"] is True


def test_save_chmods_0o600(tmp_path: Path) -> None:
    path = save_ui_state(tmp_path, dict(_DEFAULTS))
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600


def test_save_creates_parent(tmp_path: Path) -> None:
    nested = tmp_path / "nested" / "dir"
    path = save_ui_state(nested, dict(_DEFAULTS))
    assert path.exists()


def test_corrupt_file_returns_defaults(tmp_path: Path) -> None:
    (tmp_path / ".ui_state.json").write_text("not json", encoding="utf-8")
    assert load_ui_state(tmp_path) == _DEFAULTS


def test_missing_keys_get_defaults(tmp_path: Path) -> None:
    (tmp_path / ".ui_state.json").write_text(
        '{"buddy_visible": false}', encoding="utf-8",
    )
    state = load_ui_state(tmp_path)
    assert state["buddy_visible"] is False
    assert state["windows"] == {}
    assert state["zoom"] == 1.0


def test_missing_zoom_defaults_to_one(tmp_path: Path) -> None:
    """Existing installs predate the zoom field; loader must not error."""
    (tmp_path / ".ui_state.json").write_text(
        '{"buddy_visible": true, "windows": {"chat": true}}',
        encoding="utf-8",
    )
    state = load_ui_state(tmp_path)
    assert state["zoom"] == 1.0


def test_malformed_zoom_defaults_to_one(tmp_path: Path) -> None:
    """Garbage in the zoom field must not crash the loader."""
    (tmp_path / ".ui_state.json").write_text(
        '{"buddy_visible": true, "windows": {}, "zoom": "huge"}',
        encoding="utf-8",
    )
    state = load_ui_state(tmp_path)
    assert state["zoom"] == 1.0


def test_zoom_roundtrip_preserves_float(tmp_path: Path) -> None:
    save_ui_state(
        tmp_path,
        {"buddy_visible": True, "windows": {}, "zoom": 0.75},
    )
    state = load_ui_state(tmp_path)
    assert state["zoom"] == 0.75


def test_legacy_flat_keys_migrate_into_windows_dict(tmp_path: Path) -> None:
    """Pre-registry installs wrote ``chat_log_visible`` / ``news_visible``
    at the top level. Loaders migrate those into ``windows`` so a user
    upgrading doesn't lose their saved layout."""
    (tmp_path / ".ui_state.json").write_text(
        '{"buddy_visible": true, "chat_log_visible": true, "news_visible": false}',
        encoding="utf-8",
    )
    state = load_ui_state(tmp_path)
    assert state["windows"] == {"chat": True, "news": False}


def test_explicit_windows_dict_wins_over_legacy_keys(tmp_path: Path) -> None:
    """If a file contains BOTH the new ``windows`` dict and the legacy
    flat keys (mid-migration write), the explicit dict wins."""
    (tmp_path / ".ui_state.json").write_text(
        '{"buddy_visible": true, "chat_log_visible": false, '
        '"windows": {"chat": true}}',
        encoding="utf-8",
    )
    state = load_ui_state(tmp_path)
    assert state["windows"] == {"chat": True}
