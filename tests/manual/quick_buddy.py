"""Manual test: launch the buddy on the QtQuick path.

Spawns ``BuddyQuickWindow`` with ``BUDDY_IDLE`` ASCII content and the
default font/zoom. Compares against the QWidget path's Phase A baseline:

    QWidget path (in motion): body p50 = 10-11 ms, fps = 70-80
    target (Phase 2):         body p50 <= 4 ms,    fps = 240 sustained

Drag the buddy to put it in motion. Reports interval / paint /
updatePaintNode percentiles every second.

Env:
    TOKENPAL_QUICK_BUDDY_SECONDS=0   auto-quit after N seconds
"""
from __future__ import annotations

import os
import sys
import time
from collections import deque

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from tokenpal.ui.ascii_renderer import BUDDY_IDLE
from tokenpal.ui.quick.buddy_window import BuddyQuickWindow


def percentile(samples, q: float) -> float:
    if not samples:
        return 0.0
    s = sorted(samples)
    idx = min(len(s) - 1, max(0, int(len(s) * q)))
    return s[idx]


def main() -> int:
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)

    window = BuddyQuickWindow(
        frame_lines=BUDDY_IDLE,
        initial_anchor=(800.0, 400.0),
        font_family="Consolas",
        font_size=14,
    )

    intervals_ms: deque[float] = deque(maxlen=600)
    last = [time.perf_counter()]
    total = [0]

    def on_frame_swapped() -> None:
        now = time.perf_counter()
        intervals_ms.append((now - last[0]) * 1000.0)
        last[0] = now
        total[0] += 1

    window.frameSwapped.connect(on_frame_swapped)
    refresh = window.screen().refreshRate() if window.screen() else 60.0

    def report() -> None:
        if not intervals_ms:
            print("[report] no frames")
            return
        avg = sum(intervals_ms) / len(intervals_ms)
        fps = 1000.0 / avg if avg > 0 else 0.0
        ip50 = percentile(intervals_ms, 0.50)
        ip99 = percentile(intervals_ms, 0.99)
        # Render-thread updatePaintNode body
        bs = list(window.buddy_item.paint_samples_ms)
        bp50 = percentile(bs, 0.50) if bs else 0.0
        bp99 = percentile(bs, 0.99) if bs else 0.0
        # Model GUI-thread tick body (re-using BuddyWindow's profiler)
        m = window.model
        durs = list(m._tick_durations)
        tb50 = percentile(durs, 0.50) * 1000.0 if durs else 0.0
        tb99 = percentile(durs, 0.99) * 1000.0 if durs else 0.0
        print(
            f"[report] refresh={refresh:.1f}Hz fps={fps:.1f} frames={total[0]}  "
            f"interval p50={ip50:.2f}/{ip99:.2f}ms  "
            f"updatePaintNode p50={bp50:.3f}/{bp99:.3f}ms  "
            f"model.tick body p50={tb50:.2f}/{tb99:.2f}ms"
        )

    rep = QTimer()
    rep.setInterval(1000)
    rep.timeout.connect(report)
    rep.start()

    seconds = float(os.environ.get("TOKENPAL_QUICK_BUDDY_SECONDS", "0"))
    if seconds > 0:
        QTimer.singleShot(int(seconds * 1000), app.quit)

    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
