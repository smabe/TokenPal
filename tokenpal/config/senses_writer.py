"""Write [senses] toggles and [network_state] labels into config.toml.

Used by the /senses and /wifi slash commands. Changes take effect on the next
run — senses are resolved once at startup, not hot-swapped.
"""

from __future__ import annotations

import re
from pathlib import Path


def _config_path() -> Path:
    from tokenpal.tools.train_voice import _find_config_toml
    return _find_config_toml()


def set_sense_enabled(name: str, enabled: bool) -> Path:
    """Flip a single `[senses] <name> = true/false` line in config.toml.

    Creates the file and the section if missing. Returns the path written.
    """
    path = _config_path()
    value = "true" if enabled else "false"
    line = f"{name} = {value}"

    if not path.exists():
        path.write_text(f"[senses]\n{line}\n", encoding="utf-8")
        return path

    content = path.read_text(encoding="utf-8")

    pattern = rf"^{re.escape(name)}\s*=\s*(true|false)\b.*$"
    if re.search(pattern, content, re.MULTILINE):
        content = re.sub(pattern, line, content, flags=re.MULTILINE, count=1)
    elif re.search(r"^\[senses\]\s*$", content, re.MULTILINE):
        content = re.sub(
            r"^\[senses\]\s*$",
            f"[senses]\n{line}",
            content,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        content = content.rstrip() + f"\n\n[senses]\n{line}\n"

    path.write_text(content, encoding="utf-8")
    return path


def set_ssid_label(ssid_hash: str, label: str) -> Path:
    """Upsert one hash->label pair under [network_state] ssid_labels.

    The TOML inline-table form `ssid_labels = { "hash" = "label", ... }` is
    what users see in config.default.toml, so we preserve that shape.
    """
    if not re.fullmatch(r"[0-9a-f]{16}", ssid_hash):
        raise ValueError(f"expected a 16-char hex hash, got {ssid_hash!r}")

    escaped_label = label.replace("\\", "\\\\").replace('"', '\\"')
    entry = f'"{ssid_hash}" = "{escaped_label}"'

    path = _config_path()
    if not path.exists():
        path.write_text(
            f"[network_state]\nssid_labels = {{ {entry} }}\n",
            encoding="utf-8",
        )
        return path

    content = path.read_text(encoding="utf-8")

    labels_pattern = re.compile(
        r"^ssid_labels\s*=\s*\{([^}]*)\}",
        re.MULTILINE,
    )
    match = labels_pattern.search(content)

    if match:
        inner = match.group(1)
        existing = dict(
            re.findall(r'"([0-9a-f]{16})"\s*=\s*"((?:[^"\\]|\\.)*)"', inner)
        )
        existing[ssid_hash] = escaped_label
        rebuilt = ", ".join(f'"{h}" = "{v}"' for h, v in existing.items())
        content = (
            content[: match.start()]
            + f"ssid_labels = {{ {rebuilt} }}"
            + content[match.end() :]
        )
    elif re.search(r"^\[network_state\]\s*$", content, re.MULTILINE):
        content = re.sub(
            r"^\[network_state\]\s*$",
            f"[network_state]\nssid_labels = {{ {entry} }}",
            content,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        content = content.rstrip() + (
            f"\n\n[network_state]\nssid_labels = {{ {entry} }}\n"
        )

    path.write_text(content, encoding="utf-8")
    return path
