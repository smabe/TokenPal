"""Qt (PySide6) desktop frontend.

Only the physics module is safe to import without PySide6 installed —
it's pure Python. Everything else in this package imports Qt and should
be loaded lazily (e.g. from ``tokenpal.ui.qt.app.create_qt_overlay()``)
so the Textual path doesn't pay for PySide6 at startup.
"""

from __future__ import annotations

import sys

from tokenpal.util.platform import current_platform


def ensure_qapplication(existing: object | None = None) -> object:
    """Return the live ``QApplication`` — reuse if the caller supplied one,
    reuse the global instance if it's already been created, else construct
    a new one. Import Qt lazily so non-Qt overlays don't pay the PySide6
    cost just by importing this package.

    Applies ``PassThrough`` hi-DPI scaling on first construction so the
    buddy renders crisply on Retina / 4K displays without Qt nudging
    fractional scale factors to integers.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    if existing is not None and isinstance(existing, QApplication):
        return existing
    inst = QApplication.instance()
    if isinstance(inst, QApplication):
        return inst
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough,
    )
    # The "windows11" style (PySide6 6.9+) paints opaque widget
    # backgrounds that bleed through WA_TranslucentBackground, giving
    # every frameless overlay a visible gray bounding box. Fusion
    # respects the alpha channel correctly. Only override when the user
    # hasn't already requested a style via the command line.
    if current_platform() == "windows" and not any(
        a.startswith(("-style", "--style")) for a in sys.argv
    ):
        QApplication.setStyle("fusion")
    return QApplication(sys.argv)
