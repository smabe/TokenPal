"""Manual test: buddy + bubble + dock-mock + grip on the QtQuick path.

Spawns ``BuddyQuickWindow`` with the three Phase 3 followers attached
under the buddy pivot. Cycles bubble text every 5 s, paints a
placeholder dock pixmap, and prints zoom-drag deltas from the grip.
Reports interval / paint percentiles every second so we can confirm
adding followers has not regressed the Phase 2 baseline (240 fps,
updatePaintNode p50 ~ 0.1 ms during motion).

Drag the buddy or the grip dots to put the scene in motion.

Env:
    TOKENPAL_QUICK_FOLLOWERS_SECONDS=0   auto-quit after N seconds
"""
from __future__ import annotations

import os
import sys
import time
from collections import deque

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from tokenpal.ui.ascii_renderer import BUDDY_IDLE
from tokenpal.ui.quick.buddy_window import BuddyQuickWindow


_SAMPLE_LINES = [
    "hi there. just watching the cursor.",
    "you've been on this file for a while -- is it the imports again?",
    "ok i'll be quiet. wave the grip and i'll see it.",
]


def percentile(samples, q: float) -> float:
    if not samples:
        return 0.0
    s = sorted(samples)
    idx = min(len(s) - 1, max(0, int(len(s) * q)))
    return s[idx]


def _make_dock_placeholder() -> QPixmap:
    pm = QPixmap(380, 36)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(0, 0, 0, 200))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(0, 0, 380, 36, 8, 8)
    p.setPen(QColor(255, 255, 255, 200))
    p.drawText(14, 23, "  > type something to your buddy")
    p.end()
    return pm


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

    window.dock_mock_item.set_source(_make_dock_placeholder())
    window.dock_mock_item.set_visible(True)

    line_idx = [0]

    def cycle_bubble() -> None:
        window.bubble_item.show_text(_SAMPLE_LINES[line_idx[0]])
        line_idx[0] = (line_idx[0] + 1) % len(_SAMPLE_LINES)

    cycle_bubble()
    bubble_timer = QTimer()
    bubble_timer.setInterval(5000)
    bubble_timer.timeout.connect(cycle_bubble)
    bubble_timer.start()

    window.grip_item.zoom_drag_delta.connect(
        lambda dy: print(f"[grip] zoom_drag_delta dy={dy}"),
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
        bs = list(window.buddy_item.paint_samples_ms)
        bp50 = percentile(bs, 0.50) if bs else 0.0
        bp99 = percentile(bs, 0.99) if bs else 0.0
        m = window.model
        durs = list(m._tick_durations)
        tb50 = percentile(durs, 0.50) * 1000.0 if durs else 0.0
        tb99 = percentile(durs, 0.99) * 1000.0 if durs else 0.0
        print(
            f"[report] refresh={refresh:.1f}Hz fps={fps:.1f} frames={total[0]}  "
            f"interval p50={ip50:.2f}/{ip99:.2f}ms  "
            f"buddy.paint p50={bp50:.3f}/{bp99:.3f}ms  "
            f"model.tick p50={tb50:.2f}/{tb99:.2f}ms"
        )

    rep = QTimer()
    rep.setInterval(1000)
    rep.timeout.connect(report)
    rep.start()

    seconds = float(os.environ.get("TOKENPAL_QUICK_FOLLOWERS_SECONDS", "0"))
    if seconds > 0:
        QTimer.singleShot(int(seconds * 1000), app.quit)

    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
