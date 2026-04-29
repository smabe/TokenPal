"""Platform-specific polish for the Qt frontend.

Kept in one file so the OS-specific branches are easy to audit. Every
helper is a no-op on unrelated platforms, so call sites don't need
``if sys.platform == ...`` gates.
"""

from __future__ import annotations

import logging
import os
import sys

from PySide6.QtCore import Qt

log = logging.getLogger(__name__)


def buddy_overlay_flags(*, focusable: bool = False) -> Qt.WindowType:
    """Shared window-flag bundle for frameless translucent overlay surfaces.

    Frameless + always-on-top, plus ``Qt.Tool`` off-darwin for taskbar
    suppression (on macOS ``Qt.Tool`` maps to an NSWindow utility panel
    that auto-hides on app deactivate; accessory mode + collection
    behavior cover the same ground there).

    ``focusable=False`` (default) blocks focus-stealing; pass ``True``
    for interactive surfaces (chat input, dock buttons).
    """
    flags = (
        Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.WindowStaysOnTopHint
    )
    if not focusable:
        flags |= Qt.WindowType.WindowDoesNotAcceptFocus
    if sys.platform != "darwin":
        flags |= Qt.WindowType.Tool
    return flags


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


def apply_macos_stay_visible(window: object) -> None:
    """Keep ``window`` visible when the app loses focus or the user
    switches spaces on macOS.

    Qt's ``FramelessWindowHint`` + ``WindowStaysOnTopHint`` alone
    isn't enough: the NSWindow AppKit wraps ends up with the default
    collection behavior, which makes it vanish whenever the user
    clicks on the desktop or covers the buddy with another window.

    Fix: set ``CanJoinAllSpaces | Stationary | FullScreenAuxiliary``
    on the underlying NSWindow. Done in code rather than via Info.plist
    because we don't ship a bundled .app — we're a plain Python process.

    No-op off macOS or when pyobjc isn't installed.
    """
    if sys.platform != "darwin":
        return
    try:
        import objc  # noqa: PLC0415
        from AppKit import (  # noqa: PLC0415
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorStationary,
        )
    except ImportError:
        log.debug(
            "pyobjc not installed — buddy will hide on app deactivate; "
            "install the 'macos' extra to keep it visible.",
        )
        return
    try:
        # Qt's QWidget.winId() returns a void* pointer to the NSView on
        # macOS. Reconstitute it as an ObjC object and walk up to the
        # enclosing NSWindow.
        view_id = int(window.winId())  # type: ignore[attr-defined]
        view = objc.objc_object(c_void_p=view_id)
        ns_window = view.window()
        if ns_window is None:
            log.debug("NSView has no window yet; call show() first")
            return
        ns_window.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorFullScreenAuxiliary,
        )
    except Exception:
        log.exception("macOS stay-visible collection behavior failed")


def lock_macos_child_above(parent: object, child: object) -> None:
    """Make ``child`` a native NSWindow child of ``parent`` ordered
    above it. Locks z-order so ``child`` can never fall behind
    ``parent`` regardless of which NSWindow was most recently
    ``orderFront``-ed. Positions stay independent — Qt continues to
    drive the child's frame.

    Use case: the buddy and resize grip are both frameless translucent
    ``WindowStaysOnTopHint`` NSWindows at ``NSFloatingWindowLevel``.
    The grip's masked hit region is geometrically inside the buddy's
    full-rect mask, so z-order alone decides who receives clicks.
    Without this, the grip flakily falls behind the buddy and clicks
    fall through to the buddy's drag handler instead.

    **Must be called after both windows are mapped** — same constraint
    as ``apply_macos_stay_visible``. Idempotent: AppKit reorders the
    child if it's already attached.

    No-op off macOS or when pyobjc isn't installed.
    """
    if sys.platform != "darwin":
        return
    try:
        import objc  # noqa: PLC0415
        from AppKit import NSWindowAbove  # noqa: PLC0415
    except ImportError:
        log.debug(
            "pyobjc not installed — buddy resize grip may flake on z-order; "
            "install the 'macos' extra to lock it above the buddy.",
        )
        return
    try:
        parent_view_id = int(parent.winId())  # type: ignore[attr-defined]
        child_view_id = int(child.winId())  # type: ignore[attr-defined]
        parent_view = objc.objc_object(c_void_p=parent_view_id)
        child_view = objc.objc_object(c_void_p=child_view_id)
        parent_ns = parent_view.window()
        child_ns = child_view.window()
        if parent_ns is None or child_ns is None:
            log.debug("NSView has no window yet; call show() first")
            return
        parent_ns.addChildWindow_ordered_(child_ns, NSWindowAbove)
    except Exception:
        log.exception("macOS addChildWindow z-order lock failed")


def apply_macos_click_through(window: object) -> None:
    """Make a frameless transparent NSWindow pass mouse events through to
    whatever's underneath, system-wide.

    Qt's ``WA_TransparentForMouseEvents`` only prevents Qt itself from
    delivering events to the widget — the underlying NSWindow still
    swallows the click at the AppKit layer, so a click over the window
    never reaches the app below. Calling
    ``-[NSWindow setIgnoresMouseEvents:YES]`` fixes this at the native
    level.

    **Must be called after ``show()``** — the NSView has no attached
    NSWindow until the native window is mapped. Same constraint as
    ``apply_macos_stay_visible``.

    No-op off macOS or when pyobjc isn't installed.
    """
    if sys.platform != "darwin":
        return
    try:
        import objc  # noqa: PLC0415
    except ImportError:
        log.debug(
            "pyobjc not installed — click-through weather overlay can't "
            "be enabled; clicks over the sun/moon will not fall through.",
        )
        return
    try:
        view_id = int(window.winId())  # type: ignore[attr-defined]
        view = objc.objc_object(c_void_p=view_id)
        ns_window = view.window()
        if ns_window is None:
            log.debug("NSView has no window yet; call show() first")
            return
        ns_window.setIgnoresMouseEvents_(True)
    except Exception:
        log.exception("macOS click-through NSWindow setup failed")


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
