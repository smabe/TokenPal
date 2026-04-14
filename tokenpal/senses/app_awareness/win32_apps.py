"""Windows app awareness — foreground app and window title via pywin32."""

from __future__ import annotations

import logging
from typing import Any

from tokenpal.senses.app_awareness._common import sanitize_browser_title
from tokenpal.senses.base import AbstractSense, SenseReading
from tokenpal.senses.registry import register_sense

log = logging.getLogger(__name__)

# Optional imports — guarded so the module loads even without pywin32/psutil.
try:
    import win32gui
    import win32process
    _HAS_WIN32 = True
except ImportError:
    _HAS_WIN32 = False

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False

# Browser apps identified by Windows process name.
_BROWSERS: set[str] = {
    "chrome.exe", "firefox.exe", "msedge.exe", "brave.exe",
    "opera.exe", "vivaldi.exe", "chromium.exe", "arc.exe",
    "iexplore.exe", "safari.exe",
}

_FRIENDLY_NAMES: dict[str, str] = {
    "msedge": "Microsoft Edge",
    "chrome": "Google Chrome",
    "firefox": "Firefox",
    "brave": "Brave Browser",
    "opera": "Opera",
    "vivaldi": "Vivaldi",
    "explorer": "File Explorer",
    "code": "VS Code",
    "devenv": "Visual Studio",
    "windowsterminal": "Windows Terminal",
    "cmd": "Command Prompt",
    "powershell": "PowerShell",
    "pwsh": "PowerShell",
    "slack": "Slack",
    "discord": "Discord",
    "spotify": "Spotify",
    "teams": "Microsoft Teams",
    "outlook": "Outlook",
    "winword": "Microsoft Word",
    "excel": "Microsoft Excel",
    "powerpnt": "PowerPoint",
    "notepad": "Notepad",
    "notepad++": "Notepad++",
}


def _friendly_app_name(process_name: str) -> str:
    """Turn 'chrome.exe' into 'Chrome' for natural-language summaries."""
    name = process_name
    if name.lower().endswith(".exe"):
        name = name[:-4]
    return _FRIENDLY_NAMES.get(name.lower(), name.title())


@register_sense
class Win32AppAwareness(AbstractSense):
    sense_name = "app_awareness"
    platforms = ("windows",)
    priority = 100
    poll_interval_s = 2.0

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._prev_app: str = ""

    async def setup(self) -> None:
        if not _HAS_WIN32:
            log.warning("pywin32 not installed — disabling Windows app awareness")
            self.disable()
            return
        if not _HAS_PSUTIL:
            log.warning("psutil not installed — disabling Windows app awareness")
            self.disable()

    async def poll(self) -> SenseReading | None:
        if not self.enabled:
            return None

        app_name = "Unknown"
        process_name = ""
        window_title = ""

        try:
            hwnd = win32gui.GetForegroundWindow()
            if hwnd:
                raw_title = win32gui.GetWindowText(hwnd) or ""

                # Get the process name via PID
                if _HAS_PSUTIL:
                    try:
                        _, pid = win32process.GetWindowThreadProcessId(hwnd)
                        proc = psutil.Process(pid)
                        process_name = proc.name()
                        app_name = _friendly_app_name(process_name)
                    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                        # Process disappeared or access denied — fall back to title
                        app_name = "Unknown"

                window_title = sanitize_browser_title(process_name, raw_title, _BROWSERS)
        except Exception:
            # Window may have disappeared between calls; not worth crashing over
            log.debug("Failed to query foreground window", exc_info=True)

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
                "process_name": process_name,
                "window_title": window_title,
            },
            summary=summary,
            changed_from=changed_from,
        )

    async def teardown(self) -> None:
        pass
