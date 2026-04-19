"""Write [chat_log] settings into config.toml.

Used by the OptionsModal / /options slash command. Changes to max_persisted
take effect live (the app mutates cfg.chat_log.max_persisted in-memory after
the writer returns); persist / hydrate_on_start take effect on next run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tokenpal.config.toml_writer import update_config

MIN_PERSISTED = 0
MAX_PERSISTED = 5000


def clamp_max_persisted(n: int) -> int:
    """Clamp a proposed max_persisted to the allowed range."""
    return max(MIN_PERSISTED, min(MAX_PERSISTED, int(n)))


def set_max_persisted(n: int) -> Path:
    """Upsert [chat_log] max_persisted = n. Clamps to [MIN, MAX] first."""
    clamped = clamp_max_persisted(n)

    def mutate(data: dict[str, Any]) -> None:
        data.setdefault("chat_log", {})["max_persisted"] = clamped

    return update_config(mutate)
