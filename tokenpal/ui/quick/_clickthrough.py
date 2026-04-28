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
import logging
import os
import sys
from collections.abc import Callable

from PySide6.QtCore import QObject, QPointF, QTimer
from PySide6.QtQuick import QQuickWindow

log = logging.getLogger(__name__)

_GWL_EXSTYLE = -20
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_LAYERED = 0x00080000
_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_NOZORDER = 0x0004
_SWP_NOREDRAW = 0x0008
_SWP_NOACTIVATE = 0x0010
_SWP_FRAMECHANGED = 0x0020

_TRACE = bool(os.environ.get("TOKENPAL_QUICK_CLICKTHROUGH_TRACE"))

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
    u32.GetCursorPos.restype = ctypes.wintypes.BOOL
    u32.ScreenToClient.argtypes = [
        ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.POINT),
    ]
    u32.ScreenToClient.restype = ctypes.wintypes.BOOL
    # SetWindowPos was previously called without explicit argtypes;
    # default ctypes marshalling for c_void_p `None` and ints is
    # platform-dependent and on x64 was passing the second arg in a
    # way that caused the call to silently fail under some loaders
    # (no exception, no style update). Bind explicitly.
    u32.SetWindowPos.argtypes = [
        ctypes.wintypes.HWND,    # hWnd
        ctypes.wintypes.HWND,    # hWndInsertAfter
        ctypes.c_int,            # X
        ctypes.c_int,            # Y
        ctypes.c_int,            # cx
        ctypes.c_int,            # cy
        ctypes.wintypes.UINT,    # uFlags
    ]
    u32.SetWindowPos.restype = ctypes.wintypes.BOOL
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
        self._tick_log_count: int = 0
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
            ex0 = self._u32.GetWindowLongPtrW(self._hwnd, _GWL_EXSTYLE)
            log.info(
                "click-through bound to hwnd=%s, initial WS_EX=0x%08x "
                "(LAYERED=%s, TRANSPARENT=%s)",
                int(wid),
                ex0 & 0xFFFFFFFF,
                bool(ex0 & _WS_EX_LAYERED),
                bool(ex0 & _WS_EX_TRANSPARENT),
            )
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
        ex_new = (
            (ex | _WS_EX_TRANSPARENT) if want_transparent
            else (ex & ~_WS_EX_TRANSPARENT)
        )
        self._u32.SetWindowLongPtrW(self._hwnd, _GWL_EXSTYLE, ex_new)
        self._u32.SetWindowPos(
            self._hwnd,
            ctypes.wintypes.HWND(0),  # hWndInsertAfter (ignored under SWP_NOZORDER)
            0, 0, 0, 0,
            _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER
            | _SWP_NOACTIVATE | _SWP_NOREDRAW | _SWP_FRAMECHANGED,
        )
        if _TRACE or self._tick_log_count < 6:
            ex_after = (
                self._u32.GetWindowLongPtrW(self._hwnd, _GWL_EXSTYLE) & 0xFFFFFFFF
            )
            log.info(
                "click-through %s @ client=(%.0f,%.0f) WS_EX=0x%08x "
                "(TRANSPARENT=%s)",
                "TRANSPARENT" if want_transparent else "OPAQUE",
                client.x(), client.y(),
                ex_after,
                bool(ex_after & _WS_EX_TRANSPARENT),
            )
            self._tick_log_count += 1
