"""Per-category consent flags persisted at ~/.tokenpal/.consent.json.

Used by the phase 0 consent dialog and every network-touching tool/command.
One-time consent — never re-prompt per session. File written at 0o600.

Known categories (callers should use the ``Category`` constants):
    web_fetches         — any outbound HTTP from tools / senses
    location_lookups    — geocoding, IP->location, reverse geocode
    external_keyed_apis — anything requiring a user-supplied API key
    research_mode       — /research multi-step planner + fetch
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Final

log = logging.getLogger(__name__)


class Category:
    WEB_FETCHES: Final = "web_fetches"
    LOCATION_LOOKUPS: Final = "location_lookups"
    EXTERNAL_KEYED_APIS: Final = "external_keyed_apis"
    RESEARCH_MODE: Final = "research_mode"


ALL_CATEGORIES: Final[tuple[str, ...]] = (
    Category.WEB_FETCHES,
    Category.LOCATION_LOOKUPS,
    Category.EXTERNAL_KEYED_APIS,
    Category.RESEARCH_MODE,
)


def _default_path() -> Path:
    return Path.home() / ".tokenpal" / ".consent.json"


def load_consent(path: Path | None = None) -> dict[str, bool]:
    """Read consent flags. Missing file or unreadable JSON returns all-False."""
    path = path or _default_path()
    if not path.exists():
        return {c: False for c in ALL_CATEGORIES}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("consent file %s unreadable: %s — treating as unset", path, e)
        return {c: False for c in ALL_CATEGORIES}
    return {c: bool(raw.get(c, False)) for c in ALL_CATEGORIES}


def save_consent(flags: dict[str, bool], path: Path | None = None) -> Path:
    """Write consent flags to disk at 0o600. Unknown keys are dropped."""
    path = path or _default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    filtered = {c: bool(flags.get(c, False)) for c in ALL_CATEGORIES}
    path.write_text(json.dumps(filtered, indent=2), encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def has_consent(category: str, path: Path | None = None) -> bool:
    """True if *category* is granted. Unknown categories always False."""
    if category not in ALL_CATEGORIES:
        return False
    return load_consent(path).get(category, False)
