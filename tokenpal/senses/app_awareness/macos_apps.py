"""macOS app awareness — foreground app and window title via pyobjc."""

from __future__ import annotations

import logging
from typing import Any

from tokenpal.senses.base import AbstractSense, SenseReading
from tokenpal.senses.registry import register_sense

log = logging.getLogger(__name__)


@register_sense
class MacOSAppAwareness(AbstractSense):
    sense_name = "app_awareness"
    platforms = ("darwin",)
    priority = 100

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._prev_app: str = ""
        self._prev_title: str = ""

    async def setup(self) -> None:
        # Import here so non-macOS platforms don't fail
        try:
            from AppKit import NSWorkspace  # noqa: F401
            import Quartz  # noqa: F401
        except ImportError:
            log.warning("pyobjc not installed — disabling macOS app awareness")
            self.disable()

    async def poll(self) -> SenseReading | None:
        if not self.enabled:
            return None

        from AppKit import NSWorkspace
        import Quartz

        ws = NSWorkspace.sharedWorkspace()
        front_app = ws.frontmostApplication()
        app_name = front_app.localizedName() if front_app else "Unknown"

        # Get window title from Quartz
        window_title = ""
        windows = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID,
        )
        if windows:
            for w in windows:
                owner = w.get("kCGWindowOwnerName", "")
                if owner == app_name:
                    title = w.get("kCGWindowName", "")
                    if title:
                        window_title = title
                        break

        if window_title:
            summary = f'App: {app_name}, window title: "{window_title}"'
        else:
            summary = f"App: {app_name}"

        return self._reading(
            data={
                "app_name": app_name,
                "window_title": window_title,
            },
            summary=summary,
        )

    async def teardown(self) -> None:
        pass
