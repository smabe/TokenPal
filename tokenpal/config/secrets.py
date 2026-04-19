"""Local secret storage at ~/.tokenpal/.secrets.json.

Holds keys and tokens that must NOT live in config.toml (which is
machine-local but still cleartext and easier to accidentally share).
Mirrors ``tokenpal/config/consent.py``: JSON at 0o600, owner-only.

Currently stores:
    cloud_key   — Anthropic API key for /research synth. Managed via /cloud.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

log = logging.getLogger(__name__)

_CLOUD_KEY_FIELD = "cloud_key"
# Anthropic keys start with sk-ant- and are ~108 chars. We enforce a loose
# shape only — just enough to catch obvious typos at /cloud enable time
# without rejecting newer key formats we haven't seen.
_ANTHROPIC_KEY_RE = re.compile(r"^sk-ant-[A-Za-z0-9_\-]{20,}$")


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


def _write(data: dict[str, str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.chmod(path, 0o600)


def get_cloud_key(path: Path | None = None) -> str | None:
    """Return the stored Anthropic API key, or None if absent/empty."""
    key = _load(path or _default_path()).get(_CLOUD_KEY_FIELD, "").strip()
    return key or None


def set_cloud_key(key: str, path: Path | None = None) -> Path:
    """Persist *key* at 0o600. Raises ValueError on obvious shape mismatch."""
    key = key.strip()
    if not _ANTHROPIC_KEY_RE.match(key):
        raise ValueError(
            "Expected an Anthropic API key starting with 'sk-ant-' "
            "(get one at console.anthropic.com)."
        )
    p = path or _default_path()
    data = _load(p)
    data[_CLOUD_KEY_FIELD] = key
    _write(data, p)
    return p


def clear_cloud_key(path: Path | None = None) -> Path:
    """Remove the stored key. No-op if already absent."""
    p = path or _default_path()
    data = _load(p)
    if _CLOUD_KEY_FIELD in data:
        del data[_CLOUD_KEY_FIELD]
        _write(data, p)
    return p


def fingerprint(key: str) -> str:
    """Redacted identifier safe for logs + status lines: 'sk-ant-...abcd'."""
    key = key.strip()
    if not key:
        return "(none)"
    tail = key[-4:] if len(key) >= 4 else key
    return f"sk-ant-...{tail}"
