"""Write [ui] overlay preferences into config.toml.

Used by the Textual overlay's draggable chat-log divider to persist the
user's chosen width across restarts. Mirrors senses_writer.py in style.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tokenpal.config.toml_writer import update_config


def set_chat_log_width(width: int) -> Path:
    """Upsert `[ui] chat_log_width = <width>` in config.toml.

    Caller is responsible for bounds-clamping before persisting.
    """
    def mutate(data: dict[str, Any]) -> None:
        data.setdefault("ui", {})["chat_log_width"] = int(width)

    return update_config(mutate)
