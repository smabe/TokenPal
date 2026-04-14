"""Write [senses] toggles and [network_state] labels into config.toml.

Used by the /senses and /wifi slash commands. Changes take effect on the next
run — senses are resolved once at startup, not hot-swapped.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tokenpal.config.toml_writer import update_config


def set_sense_enabled(name: str, enabled: bool) -> Path:
    """Flip a single `[senses] <name> = true/false` line in config.toml.

    Creates the file and the section if missing. Returns the path written.
    """
    def mutate(data: dict[str, Any]) -> None:
        data.setdefault("senses", {})[name] = enabled

    return update_config(mutate)


def set_ssid_label(ssid_hash: str, label: str) -> Path:
    """Upsert one hash->label pair under [network_state] ssid_labels."""
    if not re.fullmatch(r"[0-9a-f]{16}", ssid_hash):
        raise ValueError(f"expected a 16-char hex hash, got {ssid_hash!r}")

    def mutate(data: dict[str, Any]) -> None:
        section = data.setdefault("network_state", {})
        labels = section.setdefault("ssid_labels", {})
        labels[ssid_hash] = label

    return update_config(mutate)


def add_watch_root(path: str) -> Path:
    """Append *path* to [filesystem_pulse] roots (no-op if already present)."""
    def mutate(data: dict[str, Any]) -> None:
        section = data.setdefault("filesystem_pulse", {})
        roots = section.setdefault("roots", [])
        if path not in roots:
            roots.append(path)

    return update_config(mutate)


def remove_watch_root(path: str) -> Path:
    """Remove *path* from [filesystem_pulse] roots (no-op if absent)."""
    def mutate(data: dict[str, Any]) -> None:
        section = data.setdefault("filesystem_pulse", {})
        roots = section.setdefault("roots", [])
        if path in roots:
            roots.remove(path)

    return update_config(mutate)
