"""Manual smoke test: QtOverlay with [ui] backend="quick".

Constructs the full QtOverlay (tray, chat, weather, dialogs all
included) with the Quick backend selected and runs for a few
seconds. Validates that the buddy + followers wire up cleanly when
the QWidget surfaces are replaced by QQuickItems.

Run (Windows PowerShell):
    set TOKENPAL_QUICK_BACKEND_SMOKE_SECONDS=4
    .venv\\Scripts\\python.exe tests\\manual\\quick_backend_smoke.py

Bash:
    TOKENPAL_QUICK_BACKEND_SMOKE_SECONDS=4 \\
        .venv/Scripts/python.exe tests/manual/quick_backend_smoke.py
"""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QTimer

from tokenpal.ui.qt.overlay import QtOverlay


def main() -> int:
    backend = os.environ.get("TOKENPAL_QUICK_BACKEND_SMOKE_BACKEND", "quick")
    cfg = {
        "backend": backend,
        "buddy_name": "TokenPal",
        "font_family": "Consolas",
        "font_size": 14,
        "position": "bottom_right",
        "chat_log_width": 40,
    }
    overlay = QtOverlay(cfg)
    overlay.setup()

    seconds = float(os.environ.get("TOKENPAL_QUICK_BACKEND_SMOKE_SECONDS", "4"))
    QTimer.singleShot(int(seconds * 1000), lambda: overlay._app.quit())  # noqa: SLF001

    overlay.run_loop()
    overlay.teardown()
    print(f"[smoke] {backend!r}-backend overlay setup + run_loop + teardown OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
