"""Pure-Python screen-bounds helpers.

Kept Qt-free so the buddy off-screen rescue logic is unit-testable
without spinning up a QGuiApplication.
"""

from __future__ import annotations

Rect = tuple[int, int, int, int]
"""``(left, top, right, bottom)`` matching ``QRect.left/top/right/bottom``."""


def offscreen_rescue_target(
    com: tuple[float, float],
    rects: list[Rect],
    margin: int = 24,
) -> tuple[float, float] | None:
    """Decide whether ``com`` needs rescuing back to a screen.

    Returns ``None`` if ``com`` is inside any rect (in bounds). Otherwise
    returns ``(x, y)``: the closest point that lies inside the nearest
    rect, inset by ``margin`` so the buddy lands a few pixels in from
    the edge instead of on the boundary itself.

    Distance is computed against the *non-inset* rect so the choice of
    "nearest screen" is based on the real geometry. The inset is only
    applied to the final target.
    """
    if not rects:
        return None

    cx, cy = com
    best: tuple[float, Rect, float, float] | None = None
    for rect in rects:
        left, top, right, bottom = rect
        nearest_x = min(max(cx, float(left)), float(right))
        nearest_y = min(max(cy, float(top)), float(bottom))
        d2 = (cx - nearest_x) ** 2 + (cy - nearest_y) ** 2
        if d2 == 0.0:
            return None
        if best is None or d2 < best[0]:
            best = (d2, rect, nearest_x, nearest_y)

    assert best is not None
    _, (left, top, right, bottom), nearest_x, nearest_y = best
    half_w = (right - left) / 2
    half_h = (bottom - top) / 2
    mx = min(float(margin), half_w)
    my = min(float(margin), half_h)
    target_x = min(max(nearest_x, left + mx), right - mx)
    target_y = min(max(nearest_y, top + my), bottom - my)
    return (target_x, target_y)
