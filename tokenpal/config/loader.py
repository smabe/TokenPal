"""Load and merge TOML config files into TokenPalConfig."""

from __future__ import annotations

import logging
import tomllib
from dataclasses import fields
from pathlib import Path
from typing import Any

from tokenpal.config.schema import (
    BrainConfig,
    LLMConfig,
    PluginsConfig,
    SensesConfig,
    TokenPalConfig,
    UIConfig,
)

log = logging.getLogger(__name__)

_SECTION_MAP: dict[str, type] = {
    "senses": SensesConfig,
    "llm": LLMConfig,
    "ui": UIConfig,
    "brain": BrainConfig,
    "plugins": PluginsConfig,
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge override into base, recursing into nested dicts."""
    merged = base.copy()
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _dict_to_dataclass(cls: type, data: dict[str, Any]) -> Any:
    """Convert a dict to a dataclass, ignoring unknown keys."""
    valid_fields = {f.name for f in fields(cls)}
    filtered = {k: v for k, v in data.items() if k in valid_fields}
    return cls(**filtered)


def load_config(
    project_root: Path | None = None,
    config_path: Path | None = None,
) -> TokenPalConfig:
    """Load config from config.default.toml, then overlay config.toml if it exists."""
    if project_root is None:
        project_root = Path.cwd()

    # Load defaults
    defaults_path = project_root / "config.default.toml"
    raw: dict[str, Any] = {}
    if defaults_path.exists():
        with open(defaults_path, "rb") as f:
            raw = tomllib.load(f)
        log.debug("Loaded defaults from %s", defaults_path)

    # Overlay user config
    user_path = config_path or (project_root / "config.toml")
    if user_path.exists():
        with open(user_path, "rb") as f:
            user_raw = tomllib.load(f)
        raw = _deep_merge(raw, user_raw)
        log.debug("Merged user config from %s", user_path)

    # Build dataclass tree
    sections: dict[str, Any] = {}
    for section_name, section_cls in _SECTION_MAP.items():
        section_data = raw.get(section_name, {})
        sections[section_name] = _dict_to_dataclass(section_cls, section_data)

    return TokenPalConfig(**sections)
