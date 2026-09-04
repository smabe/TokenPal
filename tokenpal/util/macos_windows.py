"""The on-screen window list, front to back."""

from __future__ import annotations

Window = tuple[int, str, str, int]
"""``(owner pid, owner name, window title, layer)``."""


def on_screen_windows() -> list[Window]:
    """Every on-screen window with a named owner, frontmost first, at any
    layer. The window server's and the Dock's own surfaces are skipped.

    Quartz is imported here, not at module scope, so callers can import this
    module on every host.
    """
    import Quartz  # noqa: PLC0415

    found: list[Window] = []
    raw = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID,
    )
    for window in raw or ():
        owner = window.get("kCGWindowOwnerName", "")
        if not owner or owner in ("Window Server", "Dock"):
            continue
        found.append((
            int(window.get("kCGWindowOwnerPID", 0)),
            str(owner),
            str(window.get("kCGWindowName", "") or ""),
            int(window.get("kCGWindowLayer", 999)),
        ))
    return found


def layer0_windows() -> list[Window]:
    """Normal-level app windows only (no menus, panels or floating overlays),
    frontmost first, so the first entry is the foreground app."""
    return [w for w in on_screen_windows() if w[3] == 0]
