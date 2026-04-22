"""Qt (PySide6) desktop frontend.

Only the physics module is safe to import without PySide6 installed —
it's pure Python. Everything else in this package imports Qt and should
be loaded lazily (e.g. from ``tokenpal.ui.qt.app.create_qt_overlay()``)
so the Textual path doesn't pay for PySide6 at startup.
"""

from __future__ import annotations

import sys


def ensure_qapplication(existing: object | None = None) -> object:
    """Return the live ``QApplication`` — reuse if the caller supplied one,
    reuse the global instance if it's already been created, else construct
    a new one. Import Qt lazily so non-Qt overlays don't pay the PySide6
    cost just by importing this package."""
    from PySide6.QtWidgets import QApplication
    if existing is not None and isinstance(existing, QApplication):
        return existing
    inst = QApplication.instance()
    if isinstance(inst, QApplication):
        return inst
    return QApplication(sys.argv)
