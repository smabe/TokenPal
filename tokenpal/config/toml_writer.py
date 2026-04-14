"""Shared helper for mutating config.toml.

Replaces the regex-based upsert duplication that lived in weather.py,
train_voice.py::activate_voice, and senses_writer.py. Uses tomllib to
parse and tomli_w to serialize — comments and layout are NOT preserved,
but inline-table/escaping edge cases come for free.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import tomli_w

from tokenpal.config.paths import find_config_toml


def update_config(
    mutate: Callable[[dict[str, Any]], None],
    path: Path | None = None,
) -> Path:
    """Read config.toml, pass the parsed dict to *mutate*, write it back.

    The *mutate* callback receives a mutable dict; any changes persist.
    Creates the file if it does not yet exist. Writes atomically via
    a sibling tmp file so a crash mid-write can't truncate user config.
    """
    path = path or find_config_toml()
    data: dict[str, Any] = {}
    if path.exists():
        with open(path, "rb") as f:
            data = tomllib.load(f)
    mutate(data)

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(tomli_w.dumps(data), encoding="utf-8")
    os.replace(tmp, path)
    return path
