"""Read the selected text of the app the user switched away from (macOS).

The four Accessibility calls the read needs sit behind ``AXBridge`` so tests
can drive every outcome — and assert the *order* of the calls — without
pyobjc. Every pyobjc import is inside a bridge method: this module must
import on every host so the action registry and the privacy-contract test's
AST walker can see the tool that wraps it.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Protocol

from tokenpal.actions.base import ActionResult
from tokenpal.desktop.content import DesktopContent, refuse_if_sensitive
from tokenpal.desktop.permissions import responsible_host
from tokenpal.util.macos_windows import Window, on_screen_windows
from tokenpal.util.platform import current_platform

log = logging.getLogger(__name__)

DEFAULT_MAX_CHARS = 8_000
_MESSAGING_TIMEOUT_S = 2.0
_SECURE_SUBROLE = "AXSecureTextField"

_ERR_API_DISABLED = -25211
_ERR_CANNOT_COMPLETE = -25204


@dataclass(frozen=True)
class SelectedText:
    """A successful read. ``content`` carries the text and keeps it out of
    ``repr``; the two flags feed the status line a caller shows."""

    content: DesktopContent
    whole_field: bool
    truncated: bool


class AXBridge(Protocol):
    """The whole OS surface the reader touches."""

    def windows(self) -> list[Window]:
        """Every on-screen window, front to back, at any layer."""

    def application(self, pid: int) -> Any:
        """Accessibility element for the application owning *pid*."""

    def set_timeout(self, element: Any, seconds: float) -> None:
        """Bound how long a read of *element* may block."""

    def attribute(self, element: Any, name: str) -> tuple[int, Any]:
        """``(AXError, value)`` for attribute *name* of *element*."""


class _MacAXBridge:
    def windows(self) -> list[Window]:
        return on_screen_windows()

    def application(self, pid: int) -> Any:
        import HIServices  # noqa: PLC0415

        return HIServices.AXUIElementCreateApplication(pid)

    def set_timeout(self, element: Any, seconds: float) -> None:
        import HIServices  # noqa: PLC0415

        HIServices.AXUIElementSetMessagingTimeout(element, seconds)

    def attribute(self, element: Any, name: str) -> tuple[int, Any]:
        import HIServices  # noqa: PLC0415

        err, value = HIServices.AXUIElementCopyAttributeValue(element, name, None)
        return int(err), value


def source_app(bridge: AXBridge) -> tuple[int, str, str] | None:
    """``(pid, name, title)`` of the app the user came from.

    That is the frontmost normal-level window that is not TokenPal's host.
    Under the Qt overlay the chat window is a floating panel (never at layer
    0) but it is on screen, so this process owns a window in the list and the
    frontmost normal window is already the source app. Under a terminal this
    process owns no window at all and the frontmost normal window is the
    terminal, so it is skipped.
    """
    windows = bridge.windows()
    me = os.getpid()
    normal = [(pid, name, title) for pid, name, title, layer in windows if layer == 0]
    if not normal:
        return None
    skip = {me} if any(pid == me for pid, *_ in windows) else {me, normal[0][0]}
    for pid, name, title in normal:
        if pid not in skip:
            return pid, name, title
    return None


def _string(value: Any) -> str:
    """A non-empty attribute value as text, else "". A CFRange or a number
    is treated as absent."""
    return value if isinstance(value, str) else ""


def _failed(reason: str, message: str, app: str) -> ActionResult:
    log.debug("selected-text read failed in %s: %s", app, reason)
    return ActionResult(output=message, success=False)


def read_selected_text(
    pid: int, app: str, *, max_chars: int, bridge: AXBridge
) -> SelectedText | ActionResult:
    """Selection (or whole field) of *pid*'s focused element, capped. A
    failure's ``output`` is user-facing: it may name *app*, never content."""
    app_el = bridge.application(pid)
    bridge.set_timeout(app_el, _MESSAGING_TIMEOUT_S)
    err, focused = bridge.attribute(app_el, "AXFocusedUIElement")
    if err == _ERR_API_DISABLED:
        return _failed(
            "permission",
            "TokenPal can't read other apps: grant Accessibility to "
            f"{responsible_host()} in System Settings > Privacy & "
            "Security > Accessibility.",
            app,
        )
    if err == _ERR_CANNOT_COMPLETE:
        return _failed(
            "no_response", f"{app} didn't answer the accessibility request", app
        )
    if err != 0 or focused is None:
        return _failed(
            "nothing_focused",
            f"Nothing is focused in {app}, or it doesn't expose its text while "
            "in the background. Try /proofread <text> instead.",
            app,
        )

    bridge.set_timeout(focused, _MESSAGING_TIMEOUT_S)
    _, subrole = bridge.attribute(focused, "AXSubrole")
    if _string(subrole) == _SECURE_SUBROLE:
        return _failed("secure_field", "Won't read a password field.", app)

    _, selected = bridge.attribute(focused, "AXSelectedText")
    text = _string(selected)
    whole_field = False
    if not text:
        _, value = bridge.attribute(focused, "AXValue")
        text = _string(value)
        whole_field = True
    if not text:
        return _failed(
            "empty", f"Nothing selected and the focused field in {app} is empty.", app
        )

    truncated = len(text) > max_chars
    text = text[:max_chars]
    log.debug(
        "read selection from %s: %d chars (whole_field=%s, truncated=%s)",
        app,
        len(text),
        whole_field,
        truncated,
    )
    return SelectedText(
        content=DesktopContent(text, app, "selection"),
        whole_field=whole_field,
        truncated=truncated,
    )


def capture_selection(
    *, max_chars: int = DEFAULT_MAX_CHARS, bridge: AXBridge | None = None
) -> SelectedText | ActionResult:
    """Read the selection of the app the user came from.

    Every failure — unsupported platform, missing pyobjc, no source app, a
    sensitive source app, a failed read — comes back as an
    ``ActionResult(success=False)`` whose ``output`` is shown to the user.
    """
    if current_platform() != "darwin":
        return ActionResult(
            output=(
                "Selected-text reading is only available on macOS. "
                "Try /proofread <text> instead."
            ),
            success=False,
        )
    bridge = bridge or _MacAXBridge()
    try:
        found = source_app(bridge)
        if found is None:
            return ActionResult(
                output="Couldn't tell which app you came from — no other window is on screen.",
                success=False,
            )
        pid, app, title = found
        refusal = refuse_if_sensitive(app, title)
        if refusal is not None:
            return refusal
        return read_selected_text(pid, app, max_chars=max_chars, bridge=bridge)
    except ImportError:
        return ActionResult(
            output="pyobjc is not installed — run: pip install -e '.[macos]'",
            success=False,
        )
