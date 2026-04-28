"""Phase 1 spike for plans/qt-it-quick-migration.md.

Bare-minimum QQuickWindow + QQuickItem rendering a master pixmap as a
textured quad. Validates the five go/no-go signals from the plan before
porting any production code:

    1. Frameless transparent QQuickWindow shows the pixmap with full alpha
    2. Rotation animates without tearing
    3. frameSwapped fires at the panel's refresh rate
    4. Click-through-on-transparent-pixels works (per-item alpha hit-test)
    5. updatePaintNode body p50 < 4 ms during forced rotation

Run:    python -m tests.manual.quick_spike
Quit:   close the window or wait for TOKENPAL_QUICK_SECONDS to elapse.

Env vars:
    TOKENPAL_QUICK_SECONDS=0   auto-quit after N seconds (0 = manual)
    TOKENPAL_QUICK_SPIN=1.5    deg per tick (default 1.5)
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import sys
import time
from collections import deque

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QPainter,
    QPen,
)
from PySide6.QtQuick import QQuickItem, QQuickWindow, QSGSimpleTextureNode


def make_master_pixmap(w: int = 400, h: int = 400) -> QImage:
    """Stand-in for the buddy's master pixmap.

    Real port pulls from BuddyWindow._render_art_pixmap(); for the spike
    we just need an ARGB image with non-trivial alpha so click-through
    and translucency can be visually verified."""
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.setBrush(QColor(255, 80, 80, 220))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(50, 50, w - 100, h - 100)
    p.setPen(QPen(QColor("white")))
    f = QFont("Consolas", 36)
    f.setBold(True)
    p.setFont(f)
    p.drawText(img.rect(), int(Qt.AlignmentFlag.AlignCenter), "BUDDY\nQUICK")
    p.end()
    return img


class BuddyItem(QQuickItem):
    """Textured-quad QQuickItem using QSGSimpleTextureNode.

    updatePaintNode runs on the render thread; we only read self._image
    (immutable after construction) and self.window() there."""

    def __init__(self, image: QImage):
        super().__init__()
        self._image = image
        self._texture = None
        self.setFlag(QQuickItem.Flag.ItemHasContents, True)
        self.setWidth(image.width())
        self.setHeight(image.height())
        self.setTransformOrigin(QQuickItem.TransformOrigin.Center)
        self.setAcceptedMouseButtons(
            Qt.MouseButton.LeftButton
            | Qt.MouseButton.RightButton
            | Qt.MouseButton.MiddleButton
        )
        self.update_samples_ms: deque[float] = deque(maxlen=480)

    def updatePaintNode(self, old_node, _update_data):
        t0 = time.perf_counter()
        node = old_node
        if node is None:
            node = QSGSimpleTextureNode()
            tex = self.window().createTextureFromImage(self._image)
            node.setTexture(tex)
            node.setOwnsTexture(True)
        node.setRect(QRectF(0.0, 0.0, self.width(), self.height()))
        self.update_samples_ms.append((time.perf_counter() - t0) * 1000.0)
        return node

    def contains(self, point: QPointF) -> bool:
        ix, iy = int(point.x()), int(point.y())
        if 0 <= ix < self._image.width() and 0 <= iy < self._image.height():
            return self._image.pixelColor(ix, iy).alpha() > 0
        return False

    def mousePressEvent(self, event):
        print(f"[hit] item-local=({event.position().x():.1f},{event.position().y():.1f}) "
              f"-- click-through is working: empty alpha would not have hit this item")
        event.accept()


_GWL_EXSTYLE = -20
_WS_EX_TRANSPARENT = 0x00000020
_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_NOZORDER = 0x0004
_SWP_FRAMECHANGED = 0x0020
_SWP_NOREDRAW = 0x0008
_SWP_NOACTIVATE = 0x0010


def _bind_user32():
    u32 = ctypes.windll.user32
    u32.GetWindowLongPtrW.restype = ctypes.c_longlong
    u32.GetWindowLongPtrW.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
    u32.SetWindowLongPtrW.restype = ctypes.c_longlong
    u32.SetWindowLongPtrW.argtypes = [
        ctypes.wintypes.HWND,
        ctypes.c_int,
        ctypes.c_longlong,
    ]
    u32.GetCursorPos.argtypes = [ctypes.POINTER(ctypes.wintypes.POINT)]
    u32.ScreenToClient.argtypes = [
        ctypes.wintypes.HWND,
        ctypes.POINTER(ctypes.wintypes.POINT),
    ]
    return u32


class Win32ClickThroughToggle:
    """Per-pixel click-through via WS_EX_TRANSPARENT toggle.

    QQuickWindow's DirectComposition path doesn't honor WM_NCHITTEST →
    HTTRANSPARENT for cross-process forwarding the way layered windows
    do. Instead, toggle WS_EX_TRANSPARENT on the HWND based on whether
    the cursor is over an opaque pixel. When set, ALL input passes
    through; when cleared, the window receives input normally."""

    def __init__(self, window: QQuickWindow, item: QQuickItem):
        self._window = window
        self._item = item
        self._hwnd = None
        self._u32 = _bind_user32()
        self._currently_transparent: bool | None = None
        self.transparent_ticks = 0
        self.opaque_ticks = 0

    def tick(self) -> None:
        if self._hwnd is None:
            wid = self._window.winId()
            if not wid:
                return
            self._hwnd = ctypes.wintypes.HWND(int(wid))
        pt = ctypes.wintypes.POINT()
        if not self._u32.GetCursorPos(ctypes.byref(pt)):
            return
        if not self._u32.ScreenToClient(self._hwnd, ctypes.byref(pt)):
            return
        dpr = self._window.devicePixelRatio() or 1.0
        scene = QPointF(pt.x / dpr, pt.y / dpr)
        local = self._item.mapFromScene(scene)
        opaque = self._item.contains(local)
        if opaque:
            self.opaque_ticks += 1
        else:
            self.transparent_ticks += 1
        want_transparent = not opaque
        if want_transparent == self._currently_transparent:
            return
        self._currently_transparent = want_transparent
        ex = self._u32.GetWindowLongPtrW(self._hwnd, _GWL_EXSTYLE)
        ex = (ex | _WS_EX_TRANSPARENT) if want_transparent else (ex & ~_WS_EX_TRANSPARENT)
        self._u32.SetWindowLongPtrW(self._hwnd, _GWL_EXSTYLE, ex)
        # SetWindowLongPtrW alone doesn't refresh cached input behavior;
        # SetWindowPos with SWP_FRAMECHANGED forces Windows to re-read the
        # ext style for hit-testing.
        self._u32.SetWindowPos(
            self._hwnd, None, 0, 0, 0, 0,
            _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER
            | _SWP_NOACTIVATE | _SWP_NOREDRAW | _SWP_FRAMECHANGED,
        )


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
    app = QGuiApplication(sys.argv)

    image = make_master_pixmap()

    window = QQuickWindow()
    window.setColor(QColor(Qt.GlobalColor.transparent))
    window.setFlags(
        Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.WindowStaysOnTopHint
        | Qt.WindowType.Tool
    )
    window.resize(600, 600)

    item = BuddyItem(image)
    item.setParentItem(window.contentItem())
    item.setX((600 - image.width()) / 2)
    item.setY((600 - image.height()) / 2)

    click_through = None
    click_through_timer: QTimer | None = None
    force_transparent = os.environ.get("TOKENPAL_QUICK_FORCE_TRANSPARENT") == "1"
    if sys.platform == "win32":
        click_through = Win32ClickThroughToggle(window, item)
        if not force_transparent:
            click_through_timer = QTimer()
            click_through_timer.setInterval(16)
            click_through_timer.timeout.connect(click_through.tick)
            click_through_timer.start()

        def force_apply() -> None:
            u32 = _bind_user32()
            hwnd = ctypes.wintypes.HWND(int(window.winId()))
            ex = u32.GetWindowLongPtrW(hwnd, _GWL_EXSTYLE)
            print(f"[ext-style] before=0x{ex & 0xFFFFFFFF:08x}")
            if force_transparent:
                ex |= _WS_EX_TRANSPARENT
                u32.SetWindowLongPtrW(hwnd, _GWL_EXSTYLE, ex)
                ex2 = u32.GetWindowLongPtrW(hwnd, _GWL_EXSTYLE)
                print(f"[ext-style] after force-set=0x{ex2 & 0xFFFFFFFF:08x} "
                      f"(WS_EX_TRANSPARENT bit={'on' if ex2 & _WS_EX_TRANSPARENT else 'off'})")
                # SWP_FRAMECHANGED to ensure the change takes effect
                SWP_NOMOVE = 0x0002
                SWP_NOSIZE = 0x0001
                SWP_NOZORDER = 0x0004
                SWP_FRAMECHANGED = 0x0020
                u32.SetWindowPos(
                    hwnd, None, 0, 0, 0, 0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED,
                )
        QTimer.singleShot(500, force_apply)

    spin_deg = float(os.environ.get("TOKENPAL_QUICK_SPIN", "1.5"))
    angle = [0.0]

    def step() -> None:
        angle[0] = (angle[0] + spin_deg) % 360.0
        item.setRotation(angle[0])
        item.update()

    spin = QTimer()
    spin.setInterval(0)
    spin.timeout.connect(step)
    spin.start()

    frame_intervals_ms: deque[float] = deque(maxlen=480)
    last = [time.perf_counter()]
    total = [0]

    def on_frame_swapped() -> None:
        now = time.perf_counter()
        frame_intervals_ms.append((now - last[0]) * 1000.0)
        last[0] = now
        total[0] += 1

    window.frameSwapped.connect(on_frame_swapped)

    refresh = window.screen().refreshRate() if window.screen() else 60.0

    def report() -> None:
        if not frame_intervals_ms:
            print("[report] no frames yet")
            return
        avg = sum(frame_intervals_ms) / len(frame_intervals_ms)
        fps = 1000.0 / avg if avg > 0 else 0.0
        f_p50 = percentile(frame_intervals_ms, 0.50)
        f_p99 = percentile(frame_intervals_ms, 0.99)
        u_p50 = percentile(item.update_samples_ms, 0.50)
        u_p99 = percentile(item.update_samples_ms, 0.99)
        hit_str = ""
        if click_through is not None:
            hit_str = (
                f"  cursor opaque={click_through.opaque_ticks} "
                f"transparent={click_through.transparent_ticks}"
            )
        print(
            f"[report] refresh={refresh:.1f}Hz frames={total[0]} fps={fps:.1f}  "
            f"interval p50={f_p50:.2f}ms p99={f_p99:.2f}ms  "
            f"updatePaintNode p50={u_p50:.3f}ms p99={u_p99:.3f}ms{hit_str}"
        )

    rep = QTimer()
    rep.setInterval(1000)
    rep.timeout.connect(report)
    rep.start()

    duration = float(os.environ.get("TOKENPAL_QUICK_SECONDS", "0"))
    if duration > 0:
        QTimer.singleShot(int(duration * 1000), app.quit)

    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
