"""Write [tools] opt-in tool allowlist into config.toml.

Mirrors senses_writer.py — used by the /tools picker. Default tools (timer,
system_info, open_app, do_math) are gated by [actions] and live outside this
list; only phase 1+ opt-in tools land here.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from tokenpal.config.toml_writer import update_config


def set_enabled_tools(names: Iterable[str]) -> Path:
    """Overwrite `[tools] enabled_tools = [...]` with *names*, de-duped + sorted."""
    unique = sorted({n for n in names if n})

    def mutate(data: dict[str, Any]) -> None:
        data.setdefault("tools", {})["enabled_tools"] = unique

    return update_config(mutate)


def set_tool_enabled(name: str, enabled: bool) -> Path:
    """Toggle a single tool by name, preserving the rest of the allowlist."""
    if not name:
        raise ValueError("tool name required")

    def mutate(data: dict[str, Any]) -> None:
        section = data.setdefault("tools", {})
        current = list(section.get("enabled_tools") or [])
        current_set = set(current)
        if enabled:
            current_set.add(name)
        else:
            current_set.discard(name)
        section["enabled_tools"] = sorted(current_set)

    return update_config(mutate)
