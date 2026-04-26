"""Tests for the off-screen rescue geometry helper.

Pure-logic — no Qt. Validates:
- COM inside a rect → no rescue.
- COM outside all rects → target is the nearest in-bounds point on the
  closest rect, inset by ``margin``.
- Multi-monitor seam: COM in the gap between two screens → resolves to
  the closer screen.
- Inset never produces a target outside the rect (small rect edge case).
"""

from __future__ import annotations

import pytest

from tokenpal.ui.qt._screen_bounds import offscreen_rescue_target


def test_inside_single_rect_returns_none() -> None:
    rects = [(0, 0, 1920, 1080)]
    assert offscreen_rescue_target((500.0, 500.0), rects) is None


def test_off_right_edge_targets_inset_inside_rect() -> None:
    rects = [(0, 0, 1920, 1080)]
    target = offscreen_rescue_target((2500.0, 500.0), rects, margin=24)
    assert target is not None
    tx, ty = target
    assert tx == pytest.approx(1920 - 24)
    assert ty == pytest.approx(500.0)


def test_off_top_left_corner_targets_inset_corner() -> None:
    rects = [(0, 0, 1920, 1080)]
    target = offscreen_rescue_target((-100.0, -100.0), rects, margin=20)
    assert target is not None
    tx, ty = target
    assert tx == pytest.approx(20.0)
    assert ty == pytest.approx(20.0)


def test_off_bottom_edge_clamps_y() -> None:
    rects = [(0, 0, 1920, 1080)]
    target = offscreen_rescue_target((800.0, 1500.0), rects, margin=24)
    assert target is not None
    tx, ty = target
    assert tx == pytest.approx(800.0)
    assert ty == pytest.approx(1080 - 24)


def test_inside_secondary_monitor_returns_none() -> None:
    # Primary on the left, secondary stacked to the right.
    rects = [(0, 0, 1920, 1080), (1920, 0, 3840, 1080)]
    assert offscreen_rescue_target((2500.0, 500.0), rects) is None


def test_seam_between_two_monitors_picks_closer() -> None:
    # Two screens with a 100-px vertical gap between them. Body sits in
    # the gap, slightly closer to the top one.
    rects = [(0, 0, 1920, 1080), (0, 1180, 1920, 2260)]
    target = offscreen_rescue_target((500.0, 1100.0), rects, margin=10)
    assert target is not None
    tx, ty = target
    assert tx == pytest.approx(500.0)
    assert ty == pytest.approx(1080 - 10)


def test_no_rects_returns_none() -> None:
    assert offscreen_rescue_target((100.0, 100.0), []) is None


def test_inset_clamped_when_rect_is_smaller_than_margin() -> None:
    # Pathological tiny rect — inset shouldn't flip the target outside.
    rects = [(100, 100, 110, 110)]
    target = offscreen_rescue_target((500.0, 500.0), rects, margin=24)
    assert target is not None
    tx, ty = target
    assert 100.0 <= tx <= 110.0
    assert 100.0 <= ty <= 110.0
