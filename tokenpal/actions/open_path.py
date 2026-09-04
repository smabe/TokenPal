"""Open one file from ``[paths] allowed_dirs`` with the OS default handler.

Everything opens except what the denylist, the executable bit, or an app-bundle
ancestor rules out, so a text file lands in an editor and an ``.html`` in a
browser. Nothing here ever runs a program; ``open_app`` is the tool for apps.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Any, ClassVar

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.registry import register_action
from tokenpal.config.loader import load_config
from tokenpal.util.paths import (
    allowed_roots,
    is_hidden_or_protected,
    path_is_sensitive,
    resolve_inside,
)
from tokenpal.util.platform import current_platform

log = logging.getLogger(__name__)

# Suffixes the OS hands to an interpreter, an installer, or a shell. macOS
# `open` routes .command to Terminal, .jar to JavaLauncher, .pkg to Installer
# and .workflow to Automator; the Windows half tracks PATHEXT plus the
# shortcut formats (.lnk, .url, .webloc), which redirect to arbitrary targets.
_DENIED = frozenset({
    ".action", ".app", ".applescript", ".bat", ".bundle", ".cmd", ".com",
    ".command", ".dmg", ".exe", ".fish", ".hta", ".jar", ".js", ".jse",
    ".lnk", ".mpkg", ".msc", ".msi", ".pif", ".php", ".pkg", ".pl", ".ps1",
    ".py", ".rb", ".reg", ".scpt", ".scr", ".sh", ".terminal", ".url",
    ".vbe", ".vbs", ".webloc", ".workflow", ".wsf", ".wsh", ".zsh",
})

# A directory whose name ends in one of these is an executable package: every
# file inside it, however benign its own suffix, is part of a program.
_BUNDLE_SUFFIXES = (".app", ".workflow", ".action", ".bundle")


def _refuse(detail: str) -> ActionResult:
    return ActionResult(output=detail, success=False)


def _inside_bundle(resolved: Path) -> bool:
    return any(part.lower().endswith(_BUNDLE_SUFFIXES) for part in resolved.parent.parts)


@register_action
class OpenPathAction(AbstractAction):
    action_name = "open_path"
    description = (
        "Open a document, image, or media file from [paths] allowed_dirs with its "
        "default app. Never runs programs; use open_app for applications."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to open, as returned by find_files.",
            },
        },
        "required": ["path"],
    }
    safe = False
    requires_confirm = True
    # Launching is a side effect; a cached "Opening x." would skip the launch.
    cacheable: ClassVar[bool] = False

    async def execute(self, **kwargs: Any) -> ActionResult:
        raw = kwargs.get("path")
        if not isinstance(raw, str) or not raw.strip():
            return _refuse("open_path needs a path.")

        roots = await allowed_roots(load_config().paths.allowed_dirs)
        if not roots:
            return _refuse("[paths] allowed_dirs names no folder that exists.")

        match = resolve_inside(raw, roots)
        if match is None:
            return _refuse("That path is outside [paths] allowed_dirs.")
        resolved, root, rel = match

        # Every check below reads the resolved target, never the argument: a
        # "notes.txt" symlink pointing at a shell script must be refused as the
        # script it is.
        if not resolved.exists():
            return _refuse("No such file.")
        if resolved.is_dir():
            return _refuse("open_path opens files, not folders.")
        if not resolved.is_file():
            return _refuse("open_path opens regular files only.")

        if is_hidden_or_protected(resolved, root) or path_is_sensitive(rel):
            return _refuse("That path is protected.")

        if resolved.suffix.lower() in _DENIED:
            return _refuse("open_path does not open scripts, programs, or installers.")
        if os.access(resolved, os.X_OK):
            return _refuse("That file is executable, so open_path will not open it.")
        if _inside_bundle(resolved):
            return _refuse("That file is inside an app bundle.")

        return self._launch(resolved)

    def _launch(self, resolved: Path) -> ActionResult:
        plat = current_platform()
        opener = "open" if plat == "darwin" else "xdg-open"
        try:
            if plat == "windows":
                # NotImplementedError when ShellExecute is unavailable.
                os.startfile(str(resolved))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(
                    [opener, str(resolved)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except FileNotFoundError:
            return _refuse(f"{opener} is not installed, so no app can open that file.")
        except (OSError, NotImplementedError) as e:
            log.warning("open_path failed to launch (%s): %s", plat, type(e).__name__)
            return _refuse("The system could not open that file.")

        return ActionResult(output=f"Opening {resolved.name}.")
