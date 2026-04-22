"""Qt (PySide6) desktop frontend.

Only the physics module is safe to import without PySide6 installed —
it's pure Python. Everything else in this package imports Qt and should
be loaded lazily (e.g. from ``tokenpal.ui.qt.app.create_qt_overlay()``)
so the Textual path doesn't pay for PySide6 at startup.
"""
