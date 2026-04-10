"""Open app action — launch an application by name."""

from __future__ import annotations

import logging
import subprocess
from typing import Any

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.registry import register_action
from tokenpal.util.platform import current_platform

log = logging.getLogger(__name__)

# Allowlist of safe-to-open application names (lowercase for matching).
# Only common productivity/dev apps — no system utilities, no terminals.
_ALLOWED_APPS = frozenset({
    "activity monitor",
    "calculator",
    "calendar",
    "chrome",
    "cursor",
    "discord",
    "edge",
    "finder",
    "firefox",
    "ghostty",
    "google chrome",
    "iterm",
    "iterm2",
    "messages",
    "microsoft edge",
    "music",
    "notes",
    "preview",
    "safari",
    "signal",
    "slack",
    "spotify",
    "system preferences",
    "system settings",
    "terminal",
    "visual studio code",
    "warp",
    "xcode",
    "zed",
})


@register_action
class OpenAppAction(AbstractAction):
    action_name = "open_app"
    description = "Open an application by name. Only common apps are allowed."
    parameters = {
        "type": "object",
        "properties": {
            "app_name": {
                "type": "string",
                "description": "Name of the application to open (e.g. 'Calculator', 'Spotify')",
            },
        },
        "required": ["app_name"],
    }

    async def execute(self, **kwargs: Any) -> ActionResult:
        app_name = kwargs.get("app_name", "")
        if not app_name:
            return ActionResult(output="No app name provided.", success=False)

        if app_name.lower() not in _ALLOWED_APPS:
            return ActionResult(
                output=f"'{app_name}' is not in the allowed list. Nice try.",
                success=False,
            )

        plat = current_platform()
        try:
            if plat == "darwin":
                subprocess.Popen(
                    ["open", "-a", app_name],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            elif plat == "windows":
                subprocess.Popen(
                    ["start", "", app_name],
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                return ActionResult(output=f"open_app not supported on {plat}.", success=False)
        except OSError as e:
            log.warning("Failed to open '%s': %s", app_name, e)
            return ActionResult(output=f"Failed to open '{app_name}'.", success=False)

        return ActionResult(output=f"Opening {app_name}.")
