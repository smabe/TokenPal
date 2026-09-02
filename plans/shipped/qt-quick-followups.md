# qt-quick-followups

## Context

QtQuick buddy migration shipped Phases 1–4 in `plans/shipped/qt-it-quick-migration.md`. Buddy renders via QQuickWindow + QQuickItem followers under a pivot, sustained 240 fps on Windows 11 4K @ 240 Hz. Five known follow-ups: three real bugs (Phases 1–3) plus two refactors (Phases 4–5).

Phases 1, 2, 3, and 5 shipped. Phase 4 is deferred — see Status. The `buddy_overlay_flags()` consolidation also shipped (`c376d80`); only the remaining parking-lot items are left.

## Goal

Land the remaining follow-ups so the QtQuick buddy is a solid daily-driver across the hardware we *do* have access to (Windows 11 dev box + AMD desktop).

## Non-goals

- **Retirement** of the QWidget code paths. Separate plan once cross-platform validation lands. Both backends must keep working.
- **Cross-platform validation** on macOS M-series / Linux KDE / Linux X11. Needs hardware not on this box.
- **Automated `tests/test_quick/`** unit tests. Manual smokes (`quick_buddy.py`, `quick_followers.py`, `quick_backend_smoke.py`) have caught regressions in practice; defer until a real bug demands them.
- **`docs/claude/ui.md`** doc note for the Quick path. Defer with retirement.
- **New buddy features** unrelated to Quick rendering.

## Status

- **Phase 1 — offscreen rescue** — shipped in `aaff25f` (2026-04, "Phase 1 + 2 + dock-follow fix").
- **Phase 2 — bubble z-order vs. weather** — shipped in `aaff25f`.
- **Phase 3 — multi-monitor mixed DPRs** — shipped in `6739b54` ("Phase 3 multi-monitor mixed DPRs"). Per-screen `_ScreenWindow` + reparenting on edge cross is live in `tokenpal/ui/quick/buddy_window.py`.
- **Phase 4 — off-buddy 240 fps throttle** — **deferred**. Cursor-poll click-through is good enough on the dev box; global low-level mouse hook risks AV friction without a clear payoff. Reopen if 240 fps idle drain becomes a real complaint. Files + risks captured in the parking lot below.
- **Phase 5 — BuddyCore extraction** — shipped across `56f24c6` (5a), `8108c3c` (5b), `635104a` (5c). `BuddyCore(QObject)` at `tokenpal/ui/buddy_core.py` owns physics, art geometry, lerp clock, master sprite cache, mouse-grab state, and offscreen rescue. `BuddyWindow(QWidget)` is a thin adapter that forwards the public surface; `BuddyQuickWindow` consumes `BuddyCore` directly with the `WA_DontShowOnScreen` widget hack and four monkey-patches gone. `QtOverlay._buddy` is now `BuddyWindow | BuddyCore` with a shared public surface.

## Done log

- **Phase 1 — offscreen rescue** (commit `aaff25f`): `self._model.show()` after `WA_DontShowOnScreen` so `_tick_offscreen_rescue`'s `isVisible()` guard passes.
- **Phase 2 — bubble z-order vs. weather** (commit `aaff25f`): raise `_buddy_host` on the Quick backend after `SkyWindow.show()` / `BuddyRainOverlay.show()`.
- **Phase 3 — multi-monitor mixed DPRs** (commit `6739b54`): `BuddyQuickWindow` is now a `QObject` host with one `_ScreenWindow(QQuickWindow)` per attached screen. The buddy items live under a single pivot reparented to the active screen on edge cross. Per-window `ClickThroughToggle` with closure probe. `_pick_screen` uses `QGuiApplication.screenAt`. Bonus: dock-follow fix (`_clamp_to_buddy_screen` resolves the screen from `head_world_position` instead of the hidden model widget).
- **Phase 5 — BuddyCore extraction** (commits `56f24c6`, `8108c3c`, `635104a`):
  - 5a: extracted `BuddyCore(QObject)` at `tokenpal/ui/buddy_core.py` (~600 LOC: physics, art, lerp, master sprite, drag, rescue). `BuddyWindow` became an adapter that listens to `core.position_changed` and refreshes widget geometry / mask / move / repaint.
  - 5b: `BuddyQuickWindow` holds a `BuddyCore` directly. Dropped `WA_DontShowOnScreen`, the `paintEvent = lambda` patch, `_timer.stop()`, and the `_wake_timer`/`_sleep_timer` no-op overrides. `BuddyQuickItem` takes a `BuddyCore`; mouse handlers go through public API (`begin_drag`, `set_grab_target`, `end_drag`, `right_click_handler`). `_clamped_lerp` moved into `BuddyCore.lerped_state_clamped()`. `QtOverlay._clamp_to_buddy_screen` dropped its dead `_buddy.screen()` fallback.
  - 5c: migrated `tests/test_qt_set_zoom.py`, `test_qt_edge_dock.py`, `test_qt_bubble_follow.py`, `test_qt_dock_follow.py`, `test_qt_overlay.py`, `test_qt_shell.py` to the public `BuddyCore` API. Stripped all underscore-prefixed shims from `BuddyWindow`. Kept clean public-name forwarding (`sim`, `zoom`, `art_w/h`, `cell_w`, `line_h`, `frame_lines`, `com_art`, `lerped_state`, `wake_tick_timer`, `sleep_tick_timer`) so overlay code stays backend-agnostic. `weather.py` imports `measure_block_paint_width` from `buddy_core` directly.
  - Verification: 1898/1898 tests pass (the 9 unrelated pre-existing Windows chmod / filesystem / voice failures still fail and are out of scope). Both backends pass `tests/manual/quick_backend_smoke.py`.
- **`buddy_overlay_flags()` helper** (commit `c376d80`): added `buddy_overlay_flags(*, focusable: bool = False)` in `tokenpal/ui/qt/platform.py`; routed 7 sites through it (buddy/dock_mock/_chrome/quick/weather/speech_bubble + chat/log/overlay's floating-dock toggle). Dropped `transparent_window_flags` from `_text_fx.py` and the local `_apply_transparent_window_flags` wrapper in `weather.py`. Speech bubble switched to `focusable=False` to match its `WA_ShowWithoutActivating`. Net -54 LOC.

## Parking lot

- **Phase 4 — off-buddy 240 fps throttle** (deferred): replace the cursor-poll-based `WS_EX_TRANSPARENT` toggle in `tokenpal/ui/quick/_clickthrough.py` with always-`WS_EX_TRANSPARENT` + a global low-level mouse hook (`SetWindowsHookExW(WH_MOUSE_LL, …)`) that injects clicks back to our HWND when the cursor is over an opaque pixel. Risks: AV / SmartScreen flagging the hook, and `mouse_event` / `SendInput` from inside the callback re-triggering the hook (need a re-entry flag). The alternative GPU-savings lever (suppress inactive screen QQuickWindows) was tried this session and froze launch — abandoned. Done criteria: `tests/manual/quick_followers.py` reports sustained 240 fps regardless of cursor position with click-through still working on buddy + bubble + grip.
- **Arc B390 + hybrid-iGPU translucency bug** (one specific Dell XPS 16 laptop): Quick backend renders the QQuickWindow as an opaque black box on this machine. QWidget backend works fine on the same machine. Phase 1–3 shipped fine on every other PC. Suspected root cause is cross-GPU swapchain alpha being lost when DWM composites on the iGPU but Qt's RHI binds the swapchain to the dGPU; a Settings → Display → Graphics override pinning `python.exe` to the iGPU is the next test. Not gating any phase.
- **Screen hotplug**: `BuddyQuickWindow` snapshots `QGuiApplication.screens()` at construction. Plugging or unplugging a monitor mid-session leaves the host with stale per-screen windows. Connect `QGuiApplication.screenAdded/screenRemoved` if it ever bites.
