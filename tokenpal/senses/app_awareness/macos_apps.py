"""macOS app awareness — foreground app and window title via pyobjc."""

from __future__ import annotations

import logging
from typing import Any

from tokenpal.senses.app_awareness._common import sanitize_browser_title
from tokenpal.senses.base import AbstractSense, SenseReading
from tokenpal.senses.registry import register_sense
from tokenpal.util.macos_windows import layer0_windows

log = logging.getLogger(__name__)

# Browser apps identified by macOS display name (kCGWindowOwnerName).
_BROWSERS: set[str] = {
    "google chrome", "firefox", "safari", "arc", "brave browser",
    "microsoft edge", "chromium", "opera", "vivaldi",
}


@register_sense
class MacOSAppAwareness(AbstractSense):
    sense_name = "app_awareness"
    platforms = ("darwin",)
    priority = 100
    poll_interval_s = 2.0

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._prev_app: str = ""
        self._prev_title: str = ""

    async def setup(self) -> None:
        # Import here so non-macOS platforms don't fail
        try:
            import Quartz  # noqa: F401
        except ImportError:
            log.warning("pyobjc not installed — disabling macOS app awareness")
            self.disable()

    async def poll(self) -> SenseReading | None:
        if not self.enabled:
            return None

        # The Quartz window list rather than NSWorkspace.frontmostApplication():
        # NSWorkspace is unreliable from background threads and can get stuck
        # returning the host terminal's app.
        app_name = "Unknown"
        window_title = ""
        for _pid, owner, raw_title, _layer in layer0_windows()[:1]:
            app_name = owner
            window_title = sanitize_browser_title(app_name, raw_title, _BROWSERS)

        if window_title:
            summary = f'App: {app_name}, window title: "{window_title}"'
        else:
            summary = f"App: {app_name}"

        # Track transitions for change detection
        changed_from = ""
        if app_name != self._prev_app and self._prev_app:
            changed_from = f"switched from {self._prev_app}"
        self._prev_app = app_name

        return self._reading(
            data={
                "app_name": app_name,
                "window_title": window_title,
            },
            summary=summary,
            changed_from=changed_from,
        )

    async def teardown(self) -> None:
        pass
