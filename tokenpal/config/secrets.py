"""Local secret storage at ~/.tokenpal/.secrets.json.

Holds keys and tokens that must NOT live in config.toml (which is
machine-local but still cleartext and easier to accidentally share).
Mirrors ``tokenpal/config/consent.py``: JSON at 0o600, owner-only.

JSON shape (current):
    {
        "anthropic_key": "sk-ant-...",   # /cloud anthropic enable
        "tavily_key":    "tvly-...",      # /cloud tavily enable
        "brave_key":     "BSA..."         # /cloud brave enable
    }

Legacy shape (auto-migrated on first write):
    {"cloud_key": "sk-ant-..."}   → treated as anthropic_key on read.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

# Current field names.
_ANTHROPIC_KEY_FIELD = "anthropic_key"
_TAVILY_KEY_FIELD = "tavily_key"
_BRAVE_KEY_FIELD = "brave_key"

# Legacy field name; read-side fallback only, never written.
_LEGACY_CLOUD_KEY_FIELD = "cloud_key"

# Anthropic keys start with sk-ant- and are ~108 chars. We enforce a loose
# shape only — just enough to catch obvious typos at /cloud enable time
# without rejecting newer key formats we haven't seen.
_ANTHROPIC_KEY_RE = re.compile(r"^sk-ant-[A-Za-z0-9_\-]{20,}$")

# Tavily keys are tvly-<32+ alphanumeric>. Loose check to reject obvious typos.
_TAVILY_KEY_RE = re.compile(r"^tvly-[A-Za-z0-9_\-]{16,}$")

# Brave Search API keys are opaque 32-char base64-ish strings with no stable
# prefix. Minimum-length sanity check only.
_BRAVE_KEY_RE = re.compile(r"^[A-Za-z0-9_\-]{20,}$")


def _default_path() -> Path:
    return Path.home() / ".tokenpal" / ".secrets.json"


def _load(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("secrets file %s unreadable: %s — treating as empty", path, e)
        return {}
    return {k: str(v) for k, v in raw.items() if isinstance(v, str)}


def _load_with_migration(path: Path) -> dict[str, str]:
    """Load the secrets file, applying the legacy `cloud_key` → `anthropic_key`
    migration in-memory. Does NOT write to disk; a write-path call will do that
    on the next `set_*` operation.
    """
    data = _load(path)
    legacy = data.get(_LEGACY_CLOUD_KEY_FIELD, "").strip()
    if legacy and not data.get(_ANTHROPIC_KEY_FIELD, "").strip():
        data[_ANTHROPIC_KEY_FIELD] = legacy
    return data


def _write(data: dict[str, str], path: Path) -> None:
    # Always drop the legacy field on write so the on-disk file migrates.
    data = {k: v for k, v in data.items() if k != _LEGACY_CLOUD_KEY_FIELD}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.chmod(path, 0o600)


def _get(field: str, path: Path | None) -> str | None:
    key = _load_with_migration(path or _default_path()).get(field, "").strip()
    return key or None


def _set(field: str, key: str, validator: re.Pattern[str], expectation: str,
         path: Path | None) -> Path:
    key = key.strip()
    if not validator.match(key):
        raise ValueError(expectation)
    p = path or _default_path()
    data = _load_with_migration(p)
    data[field] = key
    _write(data, p)
    return p


def _clear(field: str, path: Path | None) -> Path:
    p = path or _default_path()
    data = _load_with_migration(p)
    touched = False
    if field in data:
        del data[field]
        touched = True
    # Also wipe the legacy field if we're clearing anthropic_key, so a
    # /cloud anthropic forget actually forgets the pre-migration key too.
    if field == _ANTHROPIC_KEY_FIELD and _LEGACY_CLOUD_KEY_FIELD in data:
        del data[_LEGACY_CLOUD_KEY_FIELD]
        touched = True
    if touched:
        _write(data, p)
    return p


# ---- Anthropic key API (legacy-named wrappers stay for back-compat) --------


def get_cloud_key(path: Path | None = None) -> str | None:
    """Return the stored Anthropic API key, or None if absent/empty."""
    return _get(_ANTHROPIC_KEY_FIELD, path)


def set_cloud_key(key: str, path: Path | None = None) -> Path:
    """Persist Anthropic *key* at 0o600. Raises ValueError on shape mismatch."""
    return _set(
        _ANTHROPIC_KEY_FIELD, key, _ANTHROPIC_KEY_RE,
        "Expected an Anthropic API key starting with 'sk-ant-' "
        "(get one at console.anthropic.com).",
        path,
    )


def clear_cloud_key(path: Path | None = None) -> Path:
    """Remove the stored Anthropic key. No-op if already absent."""
    return _clear(_ANTHROPIC_KEY_FIELD, path)


# ---- Tavily key API --------------------------------------------------------


def get_tavily_key(path: Path | None = None) -> str | None:
    return _get(_TAVILY_KEY_FIELD, path)


def set_tavily_key(key: str, path: Path | None = None) -> Path:
    return _set(
        _TAVILY_KEY_FIELD, key, _TAVILY_KEY_RE,
        "Expected a Tavily API key starting with 'tvly-' "
        "(get one at app.tavily.com).",
        path,
    )


def clear_tavily_key(path: Path | None = None) -> Path:
    return _clear(_TAVILY_KEY_FIELD, path)


# ---- Brave key API ---------------------------------------------------------


def get_brave_key(path: Path | None = None) -> str | None:
    return _get(_BRAVE_KEY_FIELD, path)


def set_brave_key(key: str, path: Path | None = None) -> Path:
    return _set(
        _BRAVE_KEY_FIELD, key, _BRAVE_KEY_RE,
        "Expected a Brave Search API key (20+ alphanumeric chars). "
        "Get one at api.search.brave.com.",
        path,
    )


def clear_brave_key(path: Path | None = None) -> Path:
    return _clear(_BRAVE_KEY_FIELD, path)


# ---- Search-key bundle -----------------------------------------------------
# One entry per search backend that needs a stored key. Adding a new backend
# means: write the get/set/clear pair above, then one tuple here — the research
# pipeline picks it up automatically via load_search_keys(). The cs_gated flag
# marks keys that only activate under `cloud_search.enabled` (Tavily today);
# keys without the flag are "presence = active" (Brave today).
_SEARCH_KEY_GETTERS: tuple[
    tuple[str, Callable[[Path | None], str | None], bool], ...
] = (
    ("tavily", get_tavily_key, True),
    ("brave",  get_brave_key,  False),
)


def load_search_keys(
    cloud_search_enabled: bool, path: Path | None = None,
) -> dict[str, str]:
    """Return {backend_name: key} for every stored search-backend key.

    Gated keys (Tavily) are omitted unless *cloud_search_enabled* is True.
    Empty/missing keys are omitted. Values are ready to hand to `search_many`
    via its `api_keys=` kwarg with no further filtering.
    """
    out: dict[str, str] = {}
    for name, getter, gated in _SEARCH_KEY_GETTERS:
        if gated and not cloud_search_enabled:
            continue
        key = getter(path)
        if key:
            out[name] = key
    return out


# ---- Fingerprint -----------------------------------------------------------


def fingerprint(key: str) -> str:
    """Redacted identifier safe for logs + status lines.

    Preserves the recognized prefix (`sk-ant-`, `tvly-`) for provenance, or
    falls back to a generic `...xxxx` for opaque keys like Brave. Never
    returns the full secret.
    """
    key = (key or "").strip()
    if not key:
        return "(none)"
    tail = key[-4:] if len(key) >= 4 else key
    if key.startswith("sk-ant-"):
        return f"sk-ant-...{tail}"
    if key.startswith("tvly-"):
        return f"tvly-...{tail}"
    return f"...{tail}"
