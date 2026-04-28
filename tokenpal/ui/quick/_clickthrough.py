"""Per-pixel click-through for translucent QQuickWindow on Windows.

QQuickWindow's DirectComposition path does not honor
WM_NCHITTEST -> HTTRANSPARENT for cross-process click forwarding,
so we toggle WS_EX_TRANSPARENT on the HWND based on whether the cursor
is over an opaque pixel. SWP_FRAMECHANGED forces Windows to re-read
the ext style for hit-testing -- without it, SetWindowLongPtrW alone
leaves the cached input behavior stale.

While WS_EX_TRANSPARENT is set, Windows throttles the window's present
rate (observed ~140 Hz on a 240 Hz panel). Cleared, full refresh
returns. Net: 240 fps when the cursor is over the buddy, ~140 fps when
elsewhere -- still a clean win over the QWidget path's 70-80 fps in
motion.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import sys
from collections.abc import Callable

from PySide6.QtCore import QObject, QPointF, QTimer
from PySide6.QtQuick import QQuickWindow

_GWL_EXSTYLE = -20
_WS_EX_TRANSPARENT = 0x00000020
_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_NOZORDER = 0x0004
_SWP_NOREDRAW = 0x0008
_SWP_NOACTIVATE = 0x0010
_SWP_FRAMECHANGED = 0x0020

OpaqueProbe = Callable[[QPointF], bool]


def _bind_user32():
    u32 = ctypes.windll.user32
    u32.GetWindowLongPtrW.restype = ctypes.c_longlong
    u32.GetWindowLongPtrW.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
    u32.SetWindowLongPtrW.restype = ctypes.c_longlong
    u32.SetWindowLongPtrW.argtypes = [
        ctypes.wintypes.HWND, ctypes.c_int, ctypes.c_longlong,
    ]
    u32.GetCursorPos.argtypes = [ctypes.POINTER(ctypes.wintypes.POINT)]
    u32.ScreenToClient.argtypes = [
        ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.POINT),
    ]
    return u32


class ClickThroughToggle(QObject):
    """Polls cursor position at 60 Hz; sets/clears WS_EX_TRANSPARENT
    based on whether ``opaque_probe(client_point)`` returns True.
    No-op on non-Windows platforms."""

    def __init__(
        self,
        window: QQuickWindow,
        opaque_probe: OpaqueProbe,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._window = window
        self._probe = opaque_probe
        self._hwnd: ctypes.wintypes.HWND | None = None
        self._u32 = None
        self._currently_transparent: bool | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        if sys.platform != "win32":
            return
        self._u32 = _bind_user32()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
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
        client = QPointF(pt.x / dpr, pt.y / dpr)
        opaque = self._probe(client)
        want_transparent = not opaque
        if want_transparent == self._currently_transparent:
            return
        self._currently_transparent = want_transparent
        ex = self._u32.GetWindowLongPtrW(self._hwnd, _GWL_EXSTYLE)
        ex = (ex | _WS_EX_TRANSPARENT) if want_transparent else (ex & ~_WS_EX_TRANSPARENT)
        self._u32.SetWindowLongPtrW(self._hwnd, _GWL_EXSTYLE, ex)
        self._u32.SetWindowPos(
            self._hwnd, None, 0, 0, 0, 0,
            _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER
            | _SWP_NOACTIVATE | _SWP_NOREDRAW | _SWP_FRAMECHANGED,
        )
