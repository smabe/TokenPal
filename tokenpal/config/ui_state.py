"""Runtime UI state persisted at ``$data_dir/.ui_state.json``.

Tracks whether the buddy / chat-log / news windows are shown or
hidden so the user's toggles survive a restart. Position is not
persisted (Qt already remembers frame geometry via the stay-visible
path), only the boolean show/hide intent.

Written at ``0o600``. Corrupt or missing files fall back to defaults
(buddy visible, chat log + news hidden), matching first-launch behavior.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import TypedDict

log = logging.getLogger(__name__)

_FILENAME = ".ui_state.json"


class UiState(TypedDict):
    buddy_visible: bool
    chat_log_visible: bool
    news_visible: bool


def _default_state() -> UiState:
    return {"buddy_visible": True, "chat_log_visible": False, "news_visible": False}


def _path_for(data_dir: Path) -> Path:
    return data_dir / _FILENAME


def load_ui_state(data_dir: Path) -> UiState:
    """Read persisted UI state. Missing or unreadable file returns defaults."""
    path = _path_for(data_dir)
    if not path.exists():
        return _default_state()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("ui_state file %s unreadable: %s, using defaults", path, e)
        return _default_state()
    defaults = _default_state()
    return {
        "buddy_visible": bool(raw.get("buddy_visible", defaults["buddy_visible"])),
        "chat_log_visible": bool(
            raw.get("chat_log_visible", defaults["chat_log_visible"]),
        ),
        "news_visible": bool(raw.get("news_visible", defaults["news_visible"])),
    }


def save_ui_state(data_dir: Path, state: UiState) -> Path:
    """Write UI state to disk at 0o600."""
    path = _path_for(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "buddy_visible": bool(state.get("buddy_visible", True)),
        "chat_log_visible": bool(state.get("chat_log_visible", False)),
        "news_visible": bool(state.get("news_visible", False)),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.chmod(path, 0o600)
    return path
