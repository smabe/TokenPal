"""Filesystem paths for user config and data.

Shared by the config reader (loader.py) and the config writers
(weather.py, senses_writer.py, train_voice.py) so both sides agree on
where config.toml lives.
"""

from __future__ import annotations

from pathlib import Path

_USER_CONFIG_DIR = Path.home() / ".tokenpal"


def find_config_toml() -> Path:
    """Return the path where config.toml lives (or should be created).

    If a config.toml already exists at ~/.tokenpal/ or in CWD, return it.
    Otherwise default to CWD — creating there matches the historical
    first-run behavior and keeps project-local overrides obvious.
    """
    for candidate in (_USER_CONFIG_DIR / "config.toml", Path.cwd() / "config.toml"):
        if candidate.exists():
            return candidate
    return Path.cwd() / "config.toml"


def default_watch_roots() -> list[Path]:
    """Return the default filesystem_pulse roots: Downloads, Desktop, Documents.

    Uses ~/Downloads etc. — works on macOS and Windows. On Linux with
    localized XDG names (e.g. German "Dokumente") users must /watch add
    their actual paths manually; this function only returns paths that
    exist to avoid watching phantom dirs.
    """
    home = Path.home()
    candidates = [home / "Downloads", home / "Desktop", home / "Documents"]
    return [p for p in candidates if p.is_dir()]
