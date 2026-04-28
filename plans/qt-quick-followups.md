# qt-quick-followups

## Context

QtQuick buddy migration shipped Phases 1–4 in `plans/shipped/qt-it-quick-migration.md`. Buddy renders via QQuickWindow + QQuickItem followers under a pivot, sustained 240 fps on Windows 11 4K @ 240 Hz. Five known follow-ups: three real bugs (Phases 1–3) plus two refactors (Phases 4–5).

Phases 1, 2, and 3 shipped this session (commits `aaff25f`, `6739b54`). Phases 4 and 5 are still open.

Each remaining numbered area is independent — pick any in any order.

## Goal

Land the remaining follow-ups so the QtQuick buddy is a solid daily-driver across the hardware we *do* have access to (Windows 11 dev box + AMD desktop). Refactors that gate the eventual retire-QWidget step.

## Non-goals

- **Retirement** of the QWidget code paths. Separate plan once cross-platform validation lands. Both backends must keep working through this plan.
- **Cross-platform validation** on macOS M-series / Linux KDE / Linux X11. Needs hardware not on this box.
- **Automated `tests/test_quick/`** unit tests. Manual smokes (`quick_buddy.py`, `quick_followers.py`, `quick_backend_smoke.py`) have caught regressions in practice; defer until a real bug demands them.
- **`docs/claude/ui.md`** doc note for the Quick path. Defer with retirement.
- **New buddy features** unrelated to Quick rendering.

## Status

- **Phase 1** — shipped in `aaff25f` (2026-04, "Phase 1 + 2 + dock-follow fix").
- **Phase 2** — shipped in `aaff25f`.
- **Phase 3** — shipped in `6739b54` ("Phase 3 multi-monitor mixed DPRs"). Per-screen `_ScreenWindow` + reparenting on edge cross is live in `tokenpal/ui/quick/buddy_window.py`.
- **Phase 4** — **deferred**. Cursor-poll click-through is good enough on the dev box; global low-level mouse hook risks AV friction without a clear payoff. Reopen if 240 fps idle drain becomes a real complaint.
- **Phase 5** — shipped across 5a/5b/5c (this session). `BuddyCore(QObject)` at `tokenpal/ui/buddy_core.py` owns physics, art, lerp, master sprite, mouse-grab, offscreen rescue. `BuddyWindow(QWidget)` is a thin adapter that forwards the public surface; the Quick path consumes `BuddyCore` directly with the `WA_DontShowOnScreen` hack gone.

## Files to touch

### Phase 4 — off-buddy 240 fps throttle
- `tokenpal/ui/quick/_clickthrough.py` — replace cursor-poll-based `WS_EX_TRANSPARENT` toggle with always-`WS_EX_TRANSPARENT` + global low-level mouse hook (`SetWindowsHookExW(WH_MOUSE_LL, …)`) that injects clicks back to our HWND when the cursor is over an opaque pixel.
- TODO: investigate the `pyautogui` / `keyboard` / direct ctypes approach. Permission flags, AV behavior.
- **Note**: the alternative GPU-savings lever (suppress inactive screen QQuickWindows) was tried this session and froze launch — abandoned.

### Phase 5 — BuddyCore extraction
- *NEW* `tokenpal/ui/buddy_core.py` — non-`QWidget` class owning physics state, art geometry, lerp math, COM, position. Both QWidget `BuddyWindow` and `BuddyQuickWindow` consume it.
- `tokenpal/ui/qt/buddy_window.py` — becomes a thin `QWidget` adapter that delegates physics + geometry to a `BuddyCore` it owns.
- `tokenpal/ui/quick/buddy_window.py` — `_model` becomes a `BuddyCore` directly; no more `WA_DontShowOnScreen` widget. The reach-ins it has today (`_timer.stop()`, `_wake_timer = lambda: None`, `_sleep_timer = lambda: None`, `paintEvent = lambda: None`, manual `.show()` for `isVisible()` rescue) become explicit `BuddyCore` API or vanish.
- `tokenpal/ui/quick/buddy_item.py` — consumes `BuddyCore` instead of `BuddyWindow`. Right-click handler still works (handler signature unchanged).
- `tests/test_qt_physics.py`, `tests/test_qt_bubble_follow.py`, `tests/test_qt_dock_follow.py`, `tests/test_buddy_environment.py`, `tests/test_buddy_rescue.py` — likely need updates if `BuddyWindow` constructors change.

## Failure modes to anticipate

- **Phase 4 (global hook permissions)**: low-level mouse hooks are watched by AV / SmartScreen. Verify the hook installs without prompting on a fresh user account; if not, fall back to current toggle and document.
- **Phase 4 (event re-injection)**: `mouse_event` / `SendInput` from inside the hook callback can re-trigger the hook → infinite recursion if not gated. Need a re-entry flag.
- **Phase 5 (model-shape break)**: `BuddyWindow`'s public surface (`position_changed`, `head_world_position`, `body_angle`, etc.) is consumed by `QtOverlay` in ~45 sites. The `BuddyCore` extraction must preserve every public method or update every call site in lockstep.
- **Phase 5 (Qt Signal on non-QObject)**: `position_changed` is a `Signal` on `BuddyWindow(QWidget)`. Moving it to a non-`QWidget` `BuddyCore` requires the core to inherit from `QObject` (for Signal support) — fine, but make sure no thread-affinity assumptions break.

## Done criteria

- **Phase 4 done**: `tests/manual/quick_followers.py` reports sustained 240 fps regardless of cursor position. Click-through still works on the buddy + bubble + grip.
- **Phase 5 done**: `BuddyCore` exists as a non-`QWidget` `QObject` subclass holding physics + art state. Both `BuddyWindow` (QWidget path) and `BuddyQuickWindow` (Quick path) consume it. All existing Qt physics / follower tests still pass. No behavior change.

## Done log

- **Phase 1 — offscreen rescue** (commit `aaff25f`): `self._model.show()` after `WA_DontShowOnScreen` so `_tick_offscreen_rescue`'s `isVisible()` guard passes.
- **Phase 2 — bubble z-order vs. weather** (commit `aaff25f`): raise `_buddy_host` on the Quick backend after `SkyWindow.show()` / `BuddyRainOverlay.show()`.
- **Phase 3 — multi-monitor mixed DPRs** (commit `6739b54`): `BuddyQuickWindow` is now a `QObject` host with one `_ScreenWindow(QQuickWindow)` per attached screen. The buddy items live under a single pivot reparented to the active screen on edge cross. Per-window `ClickThroughToggle` with closure probe. `_pick_screen` uses `QGuiApplication.screenAt`. Bonus: dock-follow fix (`_clamp_to_buddy_screen` resolves the screen from `head_world_position` instead of the hidden model widget).

## Parking lot

- **Arc B390 + hybrid-iGPU translucency bug** (one specific Dell XPS 16 laptop): Quick backend renders the QQuickWindow as an opaque black box on this machine. QWidget backend works fine on the same machine. Phase 1–3 shipped fine on every other PC. Suspected root cause is cross-GPU swapchain alpha being lost when DWM composites on the iGPU but Qt's RHI binds the swapchain to the dGPU; a Settings → Display → Graphics override pinning python.exe to the iGPU is the next test. Not gating any phase.
- **`buddy_overlay_flags()` helper**: the `Frameless | StaysOnTop | DoesNotAcceptFocus | Tool` flag block appears at 7+ sites across `tokenpal/ui/qt/` and `tokenpal/ui/quick/`. Worth consolidating into a `tokenpal/ui/qt/platform.py` helper as a separate small refactor.
- **Screen hotplug**: `BuddyQuickWindow` snapshots `QGuiApplication.screens()` at construction. Plugging or unplugging a monitor mid-session leaves the host with stale per-screen windows. Connect `QGuiApplication.screenAdded/screenRemoved` if it ever bites.
