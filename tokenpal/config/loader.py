"""Load and merge TOML config files into TokenPalConfig."""

from __future__ import annotations

import logging
import tomllib
from dataclasses import fields
from pathlib import Path
from typing import Any

from tokenpal.config.schema import (
    ActionsConfig,
    BrainConfig,
    FinetuneConfig,
    LLMConfig,
    MemoryConfig,
    PathsConfig,
    PluginsConfig,
    RemoteTrainConfig,
    SensesConfig,
    ServerConfig,
    TokenPalConfig,
    UIConfig,
    WeatherConfig,
)

log = logging.getLogger(__name__)

# Project root: two levels up from this file (tokenpal/config/loader.py → project root)
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent

# User config directory
_USER_CONFIG_DIR = Path.home() / ".tokenpal"

_SECTION_MAP: dict[str, type] = {
    "senses": SensesConfig,
    "llm": LLMConfig,
    "ui": UIConfig,
    "brain": BrainConfig,
    "memory": MemoryConfig,
    "actions": ActionsConfig,
    "paths": PathsConfig,
    "plugins": PluginsConfig,
    "finetune": FinetuneConfig,
    "server": ServerConfig,
    "weather": WeatherConfig,
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


# Nested dataclass fields that need recursive conversion.
# Maps (parent_cls, field_name) → child_cls.
_NESTED_FIELDS: dict[tuple[type, str], type] = {
    (FinetuneConfig, "remote"): RemoteTrainConfig,
}


def _dict_to_dataclass(cls: type, data: dict[str, Any]) -> Any:
    """Convert a dict to a dataclass, ignoring unknown keys.

    Recursively converts nested dicts using ``_NESTED_FIELDS``.
    """
    valid_fields = {f.name for f in fields(cls)}
    filtered: dict[str, Any] = {}
    for k, v in data.items():
        if k not in valid_fields:
            continue
        child_cls = _NESTED_FIELDS.get((cls, k))
        if isinstance(v, dict) and child_cls is not None:
            filtered[k] = _dict_to_dataclass(child_cls, v)
        else:
            filtered[k] = v
    return cls(**filtered)


def _find_defaults() -> Path | None:
    """Find config.default.toml — check package root, then cwd."""
    for candidate in [_PACKAGE_ROOT / "config.default.toml", Path.cwd() / "config.default.toml"]:
        if candidate.exists():
            return candidate
    return None


def _find_user_config(config_path: Path | None, project_root: Path | None) -> Path | None:
    """Find user config.toml — explicit path, then ~/.tokenpal/, then project root, then cwd."""
    if config_path and config_path.exists():
        return config_path

    candidates = [_USER_CONFIG_DIR / "config.toml"]
    if project_root:
        candidates.append(project_root / "config.toml")
    candidates.append(Path.cwd() / "config.toml")

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def load_config(
    project_root: Path | None = None,
    config_path: Path | None = None,
) -> TokenPalConfig:
    """Load config from config.default.toml, then overlay user config.toml if found.

    Search order for defaults: package root → cwd.
    Search order for user config: explicit path → ~/.tokenpal/ → project_root → cwd.
    """
    # Load defaults
    raw: dict[str, Any] = {}
    defaults_path = _find_defaults()
    if defaults_path:
        with open(defaults_path, "rb") as f:
            raw = tomllib.load(f)
        log.debug("Loaded defaults from %s", defaults_path)

    # Overlay user config
    user_path = _find_user_config(config_path, project_root)
    if user_path:
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
