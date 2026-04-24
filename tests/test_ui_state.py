"""Tests for tokenpal/config/ui_state.py: window visibility persistence."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from tokenpal.config.ui_state import load_ui_state, save_ui_state


def test_load_missing_returns_defaults(tmp_path: Path) -> None:
    state = load_ui_state(tmp_path)
    assert state == {"buddy_visible": True, "chat_log_visible": False}


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    save_ui_state(tmp_path, {"buddy_visible": False, "chat_log_visible": True})
    assert load_ui_state(tmp_path) == {
        "buddy_visible": False,
        "chat_log_visible": True,
    }


def test_save_chmods_0o600(tmp_path: Path) -> None:
    path = save_ui_state(tmp_path, {"buddy_visible": True, "chat_log_visible": False})
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600


def test_save_creates_parent(tmp_path: Path) -> None:
    nested = tmp_path / "nested" / "dir"
    path = save_ui_state(nested, {"buddy_visible": True, "chat_log_visible": False})
    assert path.exists()


def test_corrupt_file_returns_defaults(tmp_path: Path) -> None:
    (tmp_path / ".ui_state.json").write_text("not json", encoding="utf-8")
    assert load_ui_state(tmp_path) == {
        "buddy_visible": True,
        "chat_log_visible": False,
    }


def test_missing_keys_get_defaults(tmp_path: Path) -> None:
    (tmp_path / ".ui_state.json").write_text('{"buddy_visible": false}', encoding="utf-8")
    state = load_ui_state(tmp_path)
    assert state["buddy_visible"] is False
    assert state["chat_log_visible"] is False
