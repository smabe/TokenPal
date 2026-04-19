"""Write [cloud_llm] toggles into config.toml.

Used by the /cloud slash command. The API key lives in .secrets.json (see
tokenpal/config/secrets.py), never in config.toml — this writer only
persists the non-sensitive enabled flag and model choice.

Changes take effect on the next /research call — the runner is constructed
fresh per invocation, so no restart required.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tokenpal.config.toml_writer import update_config


def set_cloud_enabled(enabled: bool) -> Path:
    """Flip `[cloud_llm] enabled = true/false` in config.toml."""
    def mutate(data: dict[str, Any]) -> None:
        data.setdefault("cloud_llm", {})["enabled"] = enabled

    return update_config(mutate)


def set_cloud_model(model: str) -> Path:
    """Upsert `[cloud_llm] model = "<model>"` in config.toml."""
    def mutate(data: dict[str, Any]) -> None:
        data.setdefault("cloud_llm", {})["model"] = model

    return update_config(mutate)


def set_cloud_plan(enabled: bool) -> Path:
    """Flip `[cloud_llm] research_plan = true/false` in config.toml."""
    def mutate(data: dict[str, Any]) -> None:
        data.setdefault("cloud_llm", {})["research_plan"] = enabled

    return update_config(mutate)
