"""Read-only macOS permission probes for desktop-content tools.

Both probes are non-prompting: they report the current TCC state and never
raise a system dialog. ``CGRequestScreenCaptureAccess`` — the prompting
sibling of the preflight call below — is deliberately never called from
this process.

The grant attaches to the *responsible* process: the ``TokenPal.app``
bundle on the Qt path (``tokenpal/ui/qt/macos_bundle.py``), and otherwise
the terminal app rather than the interpreter. Callers that surface these
answers to a user should name ``responsible_host()`` so the user knows which
entry to look for in System Settings.

pyobjc is imported inside each function: the bindings cost real time to load
and normal startup must not pay for a check only ``--validate`` and failed
content reads need.
"""

from __future__ import annotations

import logging
import os
import platform
import sys

log = logging.getLogger(__name__)


def responsible_host() -> str:
    """Name of the process a user must grant these permissions to.

    On the Qt path the buddy runs inside its own ``TokenPal.app`` bundle
    (``tokenpal/ui/qt/macos_bundle.py``) and macOS attributes the grants
    to that app. Everywhere else macOS attributes them to the responsible
    parent process (Terminal.app, iTerm2, Cursor, ...) rather than the
    python interpreter, so naming "tokenpal" sends the user hunting in
    the wrong place.

    The bundle check has to come first: ``open`` passes the launching
    shell's environment straight through, so ``TERM_PROGRAM`` is still
    set inside the bundle and would name the wrong app.
    """
    if platform.system() == "Darwin":
        from tokenpal.ui.qt.macos_bundle import (  # noqa: PLC0415
            BUNDLE_NAME,
            running_in_bundle,
        )

        if running_in_bundle():
            return BUNDLE_NAME
        return os.environ.get("TERM_PROGRAM") or sys.executable
    return sys.executable


def accessibility_granted() -> bool | None:
    """True/False on macOS; None when not macOS or pyobjc is unavailable."""
    if platform.system() != "Darwin":
        return None
    try:
        import HIServices  # noqa: PLC0415
    except ImportError:
        log.debug("HIServices unavailable; Accessibility state unknown")
        return None
    try:
        return bool(HIServices.AXIsProcessTrusted())
    except Exception:
        log.debug("AXIsProcessTrusted raised", exc_info=True)
        return None


def screen_recording_granted() -> bool | None:
    """Same contract, via Quartz.CGPreflightScreenCaptureAccess."""
    if platform.system() != "Darwin":
        return None
    try:
        import Quartz  # noqa: PLC0415
    except ImportError:
        log.debug("Quartz unavailable; Screen Recording state unknown")
        return None
    try:
        return bool(Quartz.CGPreflightScreenCaptureAccess())
    except Exception:
        log.debug("CGPreflightScreenCaptureAccess raised", exc_info=True)
        return None
