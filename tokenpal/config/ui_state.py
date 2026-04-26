"""Runtime UI state persisted at ``$data_dir/.ui_state.json``.

Tracks whether each toggleable window is shown or hidden so the
user's toggles survive a restart. The ``windows`` field is a flat
dict keyed by registered window name (``"chat"``, ``"news"``, …),
which means adding a new toggleable window requires zero changes
here — the overlay's registry decides what to persist.

Position is not stored; Qt already remembers frame geometry via
the stay-visible path. Written at ``0o600``.

Legacy flat keys (``chat_log_visible``, ``news_visible``) from
older installs are migrated into ``windows`` on load.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import TypedDict

log = logging.getLogger(__name__)

_FILENAME = ".ui_state.json"
_LEGACY_KEY_TO_NAME: dict[str, str] = {
    "chat_log_visible": "chat",
    "news_visible": "news",
}


class UiState(TypedDict):
    buddy_visible: bool
    windows: dict[str, bool]


def _default_state() -> UiState:
    return {"buddy_visible": True, "windows": {}}


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

    if not isinstance(raw, dict):
        return _default_state()

    buddy = bool(raw.get("buddy_visible", True))
    windows: dict[str, bool] = {}

    nested = raw.get("windows")
    if isinstance(nested, dict):
        for name, value in nested.items():
            if isinstance(name, str):
                windows[name] = bool(value)

    for legacy_key, name in _LEGACY_KEY_TO_NAME.items():
        if legacy_key in raw and name not in windows:
            windows[name] = bool(raw[legacy_key])

    return {"buddy_visible": buddy, "windows": windows}


def save_ui_state(data_dir: Path, state: UiState) -> Path:
    """Write UI state to disk at 0o600."""
    path = _path_for(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "buddy_visible": bool(state.get("buddy_visible", True)),
        "windows": {
            name: bool(visible)
            for name, visible in state.get("windows", {}).items()
        },
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.chmod(path, 0o600)
    return path
