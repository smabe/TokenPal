"""Read-only macOS permission probes for desktop-content tools.

Both probes are non-prompting: they report the current TCC state and never
raise a system dialog. ``CGRequestScreenCaptureAccess`` — the prompting
sibling of the preflight call below — is deliberately never called from
this process.

The grant attaches to the *host binary*, which is the running interpreter
(or the app bundle), not "tokenpal". Callers that surface these answers to a
user should name ``sys.executable`` so the user knows which entry to look
for in System Settings.

pyobjc is imported inside each function: the bindings cost real time to load
and normal startup must not pay for a check only ``--validate`` and failed
content reads need.
"""

from __future__ import annotations

import logging
import platform

log = logging.getLogger(__name__)


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
