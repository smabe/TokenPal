"""Write [chat_log] and [ui.*_font] settings into config.toml.

Used by the OptionsModal / /options slash command. Changes to max_persisted
take effect live (the app mutates cfg.chat_log.max_persisted in-memory after
the writer returns); persist / hydrate_on_start take effect on next run.
Font writers mirror the same pattern.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from tokenpal.config.schema import FontConfig
from tokenpal.config.toml_writer import update_config

MIN_PERSISTED = 0
MAX_PERSISTED = 5000
MIN_FONT_SIZE = 8
MAX_FONT_SIZE = 48

DEFAULT_BACKGROUND_COLOR = "#000000"
DEFAULT_FONT_COLOR = "#ffffff"
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def clamp_max_persisted(n: int) -> int:
    """Clamp a proposed max_persisted to the allowed range."""
    return max(MIN_PERSISTED, min(MAX_PERSISTED, int(n)))


def set_max_persisted(n: int) -> Path:
    """Upsert [chat_log] max_persisted = n. Clamps to [MIN, MAX] first."""
    clamped = clamp_max_persisted(n)

    def mutate(data: dict[str, Any]) -> None:
        data.setdefault("chat_log", {})["max_persisted"] = clamped

    return update_config(mutate)


def clamp_background_opacity(x: float) -> float:
    """Clamp a proposed background opacity to [0.0, 1.0]."""
    return max(0.0, min(1.0, float(x)))


def set_background_opacity(x: float) -> Path:
    """Upsert [chat_log] background_opacity = x. Clamps to [0, 1]."""
    clamped = clamp_background_opacity(x)

    def mutate(data: dict[str, Any]) -> None:
        data.setdefault("chat_log", {})["background_opacity"] = clamped

    return update_config(mutate)


def normalize_hex_color(s: str, *, fallback: str) -> str:
    """Return ``s`` as a lowercase ``#rrggbb`` string if it matches that shape,
    otherwise ``fallback``. Hand-edited config.toml typos should degrade to
    the default rather than crash the UI."""
    if isinstance(s, str) and _HEX_COLOR_RE.match(s):
        return s.lower()
    return fallback


def set_background_color(s: str) -> Path:
    """Upsert [chat_log] background_color = "#rrggbb". Falls back to default
    on invalid input."""
    normalized = normalize_hex_color(s, fallback=DEFAULT_BACKGROUND_COLOR)

    def mutate(data: dict[str, Any]) -> None:
        data.setdefault("chat_log", {})["background_color"] = normalized

    return update_config(mutate)


def set_font_color(s: str) -> Path:
    """Upsert [chat_log] font_color = "#rrggbb". Falls back to default on
    invalid input."""
    normalized = normalize_hex_color(s, fallback=DEFAULT_FONT_COLOR)

    def mutate(data: dict[str, Any]) -> None:
        data.setdefault("chat_log", {})["font_color"] = normalized

    return update_config(mutate)


def clamp_font_size(n: int) -> int:
    return max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, int(n)))


def set_font(section: str, cfg: FontConfig) -> Path:
    """Upsert [ui.<section>] = cfg. ``section`` is ``chat_font`` or ``bubble_font``."""

    def mutate(data: dict[str, Any]) -> None:
        ui = data.setdefault("ui", {})
        ui[section] = asdict(cfg)

    return update_config(mutate)
