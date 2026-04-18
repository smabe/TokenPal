"""Write [idle_tools] toggles into config.toml.

Used by the /idle_tools slash command. Changes take effect on the next run —
the roller's rule set is resolved once at Brain construction.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tokenpal.config.toml_writer import update_config


def set_idle_tools_enabled(enabled: bool) -> Path:
    """Flip `[idle_tools] enabled = true|false`. Creates the section if missing."""
    def mutate(data: dict[str, Any]) -> None:
        data.setdefault("idle_tools", {})["enabled"] = enabled

    return update_config(mutate)


def set_idle_rule_enabled(rule_name: str, enabled: bool) -> Path:
    """Upsert one rule toggle under `[idle_tools.rules]`."""
    def mutate(data: dict[str, Any]) -> None:
        section = data.setdefault("idle_tools", {})
        rules = section.setdefault("rules", {})
        rules[rule_name] = enabled

    return update_config(mutate)
