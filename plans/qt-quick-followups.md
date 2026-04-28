# qt-quick-followups

## Context

QtQuick buddy migration shipped Phases 1–4 in `plans/shipped/qt-it-quick-migration.md`. Buddy renders via single `QQuickWindow` + `QQuickItem` followers under a pivot, sustained 240 fps on Windows 11 4K @ 240 Hz. Five known follow-ups remain — three real bugs, two refactors. Phase 5 (default flip + retire QWidget) is *not* in this plan; it's gated on cross-platform validation we don't have on this box.

Each numbered area is independent — pick any in any order. Phase 1 is a one-line bug fix. Phase 3 is the heavy lift.

## Goal

Land the five known follow-ups so the QtQuick buddy is a solid daily-driver across the hardware we *do* have access to (Windows 11 dev box + AMD desktop). Bugs first, then refactors that gate the eventual retire-QWidget step.

## Non-goals

- **Phase 5 retirement** of the QWidget code paths. Separate plan once cross-platform validation lands. Both backends must keep working through this plan.
- **Cross-platform validation** on macOS M-series / Linux KDE / Linux X11. Needs hardware not on this box.
- **Automated `tests/test_quick/`** unit tests. Manual smokes (`quick_buddy.py`, `quick_followers.py`, `quick_backend_smoke.py`) have caught regressions in practice; defer until a real bug demands them.
- **`docs/claude/ui.md`** doc note for the Quick path. Defer with retirement.
- **New buddy features** unrelated to Quick rendering.
- **Reverting Phase 1–4 architectural choices** (single `QQuickWindow`, hidden `BuddyWindow` as model, follower `QQuickItem`s under the pivot, `WS_EX_TRANSPARENT` toggle for click-through).

## Files to touch

Listed by phase. Some phases may touch additional files — flag at the start of each phase per the "before touching a file" rule.

### Phase 1 — offscreen rescue
- `tokenpal/ui/quick/buddy_window.py` — call `self._model.show()` after `WA_DontShowOnScreen` so `_tick_offscreen_rescue`'s `isVisible()` guard passes. Logical-only show; produces no native window.

### Phase 2 — bubble z-order vs. weather
- `tokenpal/ui/qt/overlay.py` — when on Quick backend and weather is enabled, raise `_buddy_host` after `SkyWindow.show()` / `BuddyRainOverlay.show()` so the QQuickWindow stays above the weather QWidgets.
- *Maybe* `tokenpal/ui/quick/buddy_window.py` — if a `raise_()` shim is needed.

### Phase 3 — multi-monitor with mixed DPRs (the heavy one)
- `tokenpal/ui/quick/buddy_window.py` — biggest change. Refactor from single `QQuickWindow` covering primary screen to one `QQuickWindow` *per screen* with reparenting of `BuddyQuickItem` + follower items on edge cross. Each window gets its own click-through toggle and pivot.
- `tokenpal/ui/quick/_clickthrough.py` — toggle has to track the active window (the one currently hosting the buddy item).
- `tokenpal/ui/qt/overlay.py` — `_buddy_host` becomes a manager over multiple QQuickWindows; `show()`/`hide()` broadcasts.
- `tokenpal/ui/qt/buddy_window.py` — `_screen_rects()` already iterates all screens; verify the rescue + edge-dock + drag math against the new architecture.
- TODO: investigate whether the simulator needs awareness of which screen owns the buddy at any given tick.

### Phase 4 — off-buddy 240 fps throttle
- `tokenpal/ui/quick/_clickthrough.py` — replace cursor-poll-based `WS_EX_TRANSPARENT` toggle with always-`WS_EX_TRANSPARENT` + global low-level mouse hook (`SetWindowsHookExW(WH_MOUSE_LL, …)`) that injects clicks back to our HWND when the cursor is over an opaque pixel.
- TODO: investigate the `pyautogui` / `keyboard` / direct ctypes approach. Permission flags, AV behavior.

### Phase 5 — BuddyCore extraction
- *NEW* `tokenpal/ui/buddy_core.py` — non-`QWidget` class owning physics state, art geometry, lerp math, COM, position. Both QWidget `BuddyWindow` and `BuddyQuickWindow` consume it.
- `tokenpal/ui/qt/buddy_window.py` — becomes a thin `QWidget` adapter that delegates physics + geometry to a `BuddyCore` it owns.
- `tokenpal/ui/quick/buddy_window.py` — `_model` becomes a `BuddyCore` directly; no more `WA_DontShowOnScreen` widget. The `_buddy_host` is enough.
- `tokenpal/ui/quick/buddy_item.py` — consumes `BuddyCore` instead of `BuddyWindow`. Right-click handler still works (handler signature unchanged).
- `tests/test_qt_physics.py`, `tests/test_qt_bubble_follow.py`, `tests/test_qt_dock_follow.py`, `tests/test_buddy_environment.py`, `tests/test_buddy_rescue.py` — likely need updates if `BuddyWindow` constructors change.

## Failure modes to anticipate

- **Phase 1**: `self._model.show()` with `WA_DontShowOnScreen` set may still emit `showEvent` to other code paths that subscribe (timers wake, etc.). Verify nothing on the model side changes its behavior because of the visibility flip.
- **Phase 2**: raising `_buddy_host` after weather shows could flicker if Qt processes `SetWindowPos` async; or might require periodic re-raise if weather animations dirty z-order. Test on the dev panel where presents are at 240 Hz — flicker is more visible.
- **Phase 3 (texture migration)**: when buddy crosses a screen edge, the `BuddyQuickItem`'s `QSGTexture` lives in the source window's scene graph. Reparenting an item across `QQuickWindow`s requires destroying the texture and recreating it in the target window's render thread. Risk of one-frame flash of empty buddy. Mitigation: keep both windows visible during the swap, hide source after target paints.
- **Phase 3 (dual click-through)**: while reparenting, BOTH QQuickWindows may briefly accept clicks. The toggle's hwnd-binding logic assumes one window — needs a per-window instance.
- **Phase 3 (DPR snapshot)**: the buddy art's pixmap is rasterized at master-DPR; if it crosses to a screen with a different DPR, the texture quad is sampled at a different physical scale. Master-DPR may need to be max-of-all-screens, or per-screen rasterization.
- **Phase 4 (global hook permissions)**: low-level mouse hooks are watched by AV / SmartScreen. Verify the hook installs without prompting on a fresh user account; if not, fall back to current toggle and document.
- **Phase 4 (event re-injection)**: `mouse_event` / `SendInput` from inside the hook callback can re-trigger the hook → infinite recursion if not gated. Need a re-entry flag.
- **Phase 5 (model-shape break)**: `BuddyWindow`'s public surface (`position_changed`, `head_world_position`, `body_angle`, etc.) is consumed by `QtOverlay` in ~45 sites. The `BuddyCore` extraction must preserve every public method or update every call site in lockstep.
- **Phase 5 (Qt Signal on non-QObject)**: `position_changed` is a `Signal` on `BuddyWindow(QWidget)`. Moving it to a non-`QWidget` `BuddyCore` requires the core to inherit from `QObject` (for Signal support) — fine, but make sure no thread-affinity assumptions break.

## Done criteria

- **Phase 1 done**: drag buddy fully off every screen → he tweens back home within `_OFFSCREEN_RESCUE_DURATION_S` on Quick backend. Verified manually.
- **Phase 2 done**: with `[senses] weather = true` and `[ui] backend = "quick"`, the speech bubble paints in front of the sky/rain when both are on screen.
- **Phase 3 done**: buddy can be dragged from a 4K @ 200% screen to a 1440p @ 100% screen (and back) without disjoint / double-composite render. The buddy's physical size on screen may differ between the two (master pixmap is one resolution) but should be intact, not pixelated. Click-through still works on both screens.
- **Phase 4 done**: `tests/manual/quick_followers.py` reports sustained 240 fps regardless of cursor position. Click-through still works on the buddy + bubble + grip.
- **Phase 5 done**: `BuddyCore` exists as a non-`QWidget` `QObject` subclass holding physics + art state. Both `BuddyWindow` (QWidget path) and `BuddyQuickWindow` (Quick path) consume it. All existing Qt physics / follower tests still pass. No behavior change.

## Parking lot

(empty at start)
