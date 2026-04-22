"""Platform-specific polish for the Qt frontend.

Kept in one file so the OS-specific branches are easy to audit. Every
helper is a no-op on unrelated platforms, so call sites don't need
``if sys.platform == ...`` gates.
"""

from __future__ import annotations

import logging
import os
import sys

log = logging.getLogger(__name__)


def apply_macos_accessory_mode() -> None:
    """Hide the Dock icon on macOS so TokenPal lives in the menu bar
    only. Equivalent to ``LSUIElement = true`` in an Info.plist, except
    we're not packaged — we're running from the Python interpreter.

    **MUST be called after ``QApplication`` is constructed.** The
    ``NSApplication.sharedApplication()`` call hands us the Cocoa app
    Qt already built; if Qt hasn't built it yet, we'd either create a
    throwaway NSApplication (whose policy gets clobbered when Qt
    constructs its own) or no-op. The caller in ``overlay.setup()``
    orders this correctly — don't move it.

    Uses pyobjc (``pyobjc-framework-Cocoa``) which is already a macOS
    extra. Silent no-op when pyobjc isn't present or we're not on
    macOS so the Textual / headless paths don't pay the import cost.
    """
    if sys.platform != "darwin":
        return
    try:
        from AppKit import (  # noqa: PLC0415
            NSApplication,
            NSApplicationActivationPolicyAccessory,
        )
    except ImportError:
        log.debug(
            "pyobjc not installed — Dock icon will show; install the "
            "'macos' extra to suppress it.",
        )
        return
    try:
        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyAccessory,
        )
    except Exception:
        log.exception("macOS accessory-mode activation policy failed")


def warn_wayland_limitations() -> None:
    """One-time INFO log that ``WindowStaysOnTopHint`` is advisory
    under Wayland — some compositors honor it, many don't. The buddy
    still works, it just might fall behind other windows. No action
    required from the user; this is purely informational."""
    if sys.platform != "linux":
        return
    session = os.environ.get("XDG_SESSION_TYPE", "").lower()
    wayland_display = os.environ.get("WAYLAND_DISPLAY")
    if session == "wayland" or wayland_display:
        log.info(
            "Wayland session detected — always-on-top is compositor-"
            "dependent (works on KDE / sway, inconsistent on GNOME).",
        )
