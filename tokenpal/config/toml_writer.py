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


def canon_server_url(url: str) -> str:
    """Canonical key for per-server config dicts.

    Strips trailing slashes and ensures the ``/v1`` suffix so the same host
    keyed in different shapes (``http://h:11434``, ``.../v1``, ``.../v1/``)
    collapses to one entry. Mirrors the normalization `/server switch` does
    in `tokenpal/app.py` so lookups agree with what gets persisted.
    """
    u = url.strip().rstrip("/")
    if not u.endswith("/v1"):
        u += "/v1"
    return u


def remember_server_model(url: str, model: str, path: Path | None = None) -> Path:
    """Persist the remembered model for *url* into ``[llm.per_server_models]``."""
    key = canon_server_url(url)

    def _mutate(data: dict[str, Any]) -> None:
        llm = data.setdefault("llm", {})
        mapping = llm.setdefault("per_server_models", {})
        mapping[key] = model

    return update_config(_mutate, path=path)


def remember_server_max_tokens(url: str, n: int, path: Path | None = None) -> Path:
    """Persist the remembered max_tokens for *url* into ``[llm.per_server_max_tokens]``."""
    key = canon_server_url(url)

    def _mutate(data: dict[str, Any]) -> None:
        llm = data.setdefault("llm", {})
        mapping = llm.setdefault("per_server_max_tokens", {})
        mapping[key] = int(n)

    return update_config(_mutate, path=path)
