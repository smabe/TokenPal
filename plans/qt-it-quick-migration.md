# qt-it-quick-migration

## Context

Phase A of `plans/qt-it.md` shipped:
- A1 — rain overlay paint coupled to motion (rotating shadow #1 fixed)
- body_angle returns lerped theta + per-pump paint clock (slot/paint sync)
- A4 — synchronous repaint() (paint coalescing fixed)
- Explicit composition_clear in fixed-size follower paintEvents (rotating shadow #2 fixed)

Followers track the buddy frame-coherently. Ghost is gone. Buddy still jitters during rotation on a 4K @ 240 Hz panel. Tick profile during motion:

| metric | idle | motion |
|---|---|---|
| body p50 | 5.7 ms | **10–11 ms** |
| body p99 | 9.8 ms | 13–35 ms |
| FPS | 140+ | **70–80** |

Pump period is 6 ms; body is 4–5 ms over budget every tick during motion, so pumps drop and the buddy updates at ~one third the panel's refresh rate. `TOKENPAL_FAST_PIXMAP=1` (drop bilinear) made no measurable difference — confirmed bilinear sampling wasn't the bottleneck.

The remaining cost is structural to the `QWidget + WA_TranslucentBackground` Windows path:
- Backbuffer → `UpdateLayeredWindow` → DWM
- No swapchain, no MPO promotion, no GPU compositing hook
- `UpdateLayeredWindow` BitBlt of a 23 MB bitmap to system memory per paint
- The user's hardware (4070, 9070 XT, M-series Macs) has plenty of GPU sitting idle while the CPU paints

`plans/qt-it-research.md` Phase B already named the structural fix and disqualified every alternative: **single-window QtQuick**.

## Goal

Migrate the buddy + rotating followers (bubble, dock_mock, grip) into one `QQuickWindow` with child `QQuickItem`s. DirectComposition + flip-model swapchain replaces the layered-window CPU path; the threaded scene graph paints on the render thread; `QQuickWindow.frameSwapped` provides real vsync timing.

**Targets** (4K @ 240 Hz on the dev panel):
- Tick body p50 ≤ 4 ms during motion
- Sustained 240 fps with no perceptible jitter on rotation
- All Phase A test invariants still pass

## Non-goals

- **Multi-window QtQuick** — research disqualified. Multi-window mode falls back to a single system-timer driver and provides no smoothness benefit. Single window is non-negotiable.
- **Touching physics** — `RigidBodySimulator` and the Fix-Your-Timestep accumulator are decoupled and stay unchanged. Lerp / paint-clock contract carries over.
- **Migrating tray, dialogs, voice modal, chat history, news window** — these stay on QWidget. Qt allows mixing `QWidget` and `QQuickWindow` in one app; no need to port what isn't on the smoothness hot path.
- **Migrating weather (sky + rain overlay)** — already smooth on the QWidget path; not in scope unless single-window collapse adds value.
- **Cross-backend feature drift** — every current buddy feature must be preserved (per-pixel transparency, click-through, drag/fling/zoom, voice frame swap, multi-monitor, edge dock, offscreen rescue).
- **Other backend candidates** — research disqualified web stack, game engines, native+Qt hybrid, translucent QOpenGLWidget. Don't relitigate.

## Files to touch

- `tokenpal/ui/quick/__init__.py` — new package
- `tokenpal/ui/quick/buddy_item.py` — `QQuickItem` subclass, `updatePaintNode` returning a `QSGSimpleTextureNode` wrapping the master pixmap; rotation + scale via the item's `transform`
- `tokenpal/ui/quick/bubble_item.py` — `QQuickItem` for the speech bubble (rounded rect + text node); rotation around the tail anchor
- `tokenpal/ui/quick/dock_mock_item.py` — textured-quad item for the dock-mock pixmap snapshot
- `tokenpal/ui/quick/grip_item.py` — small rotating item with the grip dots
- `tokenpal/ui/quick/window.py` — `QQuickWindow` host. `setColor(Qt.transparent)`, `WindowStaysOnTopHint`, `WindowDoesNotAcceptFocus`, click-through routing through per-item alpha hit-test
- `tokenpal/ui/quick/_paint_clock.py` — vsync-driven paint clock fed by `QQuickWindow.frameSwapped` (replaces the `now + 1/refresh` approximation in `tokenpal/ui/qt/buddy_window.py`)
- `tokenpal/ui/qt/overlay.py` — backend dispatch: instantiate Quick or QWidget buddy stack based on config
- `tokenpal/config/schema.py` — add `[ui] backend = "qt" | "quick"` (default "qt" until parity proven)
- `tokenpal/ui/qt/buddy_window.py`, `speech_bubble.py`, `dock_mock.py`, `_chrome.py` — preserve as the QWidget fallback; no removal in this plan
- `tests/test_quick/test_buddy_item.py` — headless render test (offscreen `QQuickWindow.grabWindow()`), parity check against the Qt path's master pixmap output
- `tests/test_quick/test_window_translucency.py` — verify `setColor(Qt.transparent)` + click-through on Windows + macOS

## Approach (staged)

### Phase 1 — Spike (validate the path) — ✅ DONE 2026-04-28

`tests/manual/quick_spike.py` validated all five go/no-go signals on the Windows dev box (RTX 4070, 4K @ 240 Hz):

1. ✅ Frameless transparent QQuickWindow shows pixmap with full alpha
2. ✅ Rotation tear-free
3. ✅ `frameSwapped` sustained at 240.1 Hz (== `QScreen.refreshRate()`)
4. ✅ Click-through-on-transparent — but **NOT via `WM_NCHITTEST` → `HTTRANSPARENT`**. QQuickWindow's DirectComposition path ignores HTTRANSPARENT for cross-process forwarding. The working recipe is **toggle `WS_EX_TRANSPARENT` on the HWND dynamically** based on cursor-vs-alpha sampling at ~60 Hz, plus `SetWindowPos(SWP_FRAMECHANGED)` to force Windows to re-read the ext style. When set, all input passes through; when cleared, the window receives input. Production code in Phase 2 must adopt this; the QWidget path's "free" layered-window per-pixel hit-test does not carry over. **Side effect:** while `WS_EX_TRANSPARENT` is set, Windows throttles the window's present rate to ~140 Hz (cursor over transparent area). When the cursor is over the buddy, full 240 Hz returns. Still a clean win over Phase A's 70–80 fps in motion, but Phase 2 should consider hysteresis or a coarser hit-region to keep the buddy at full refresh more of the time.
5. ✅ `updatePaintNode` p50 = 0.01 ms during forced rotation (target was <4 ms — three orders of magnitude under budget)

Cross-platform validation (macOS M-series, Linux Wayland-KDE, Linux X11) deferred to Phase 4 backend-dispatch testing — Windows being the primary target and the box where Phase A jitter was measured.

### Phase 2 — Buddy port — ✅ DONE 2026-04-28

`tokenpal/ui/quick/buddy_window.py` + `buddy_item.py` + `_clickthrough.py` ship a working buddy on the QtQuick path with the actual `BUDDY_IDLE` ASCII art, real physics, drag input, and click-through. Validated via `tests/manual/quick_buddy.py`.

**Final architecture (departures from the original plan):**

1. **Fixed window covering the primary screen; buddy moves *inside* via QQuickItem position.** The original plan implied the QQuickWindow would size + move to the rotated-art AABB like the QWidget path. That was wrong: `setPosition()` per frame stalls the Windows compositor (visible as 7-15 ms vsync gaps + microsecond catch-up bursts in the trace, then a 2-frame θ jump in one paint = "skipping a beat"). The standard game-engine pattern — *fixed window, content moves inside* — eliminates the stall. **All Phase 3 followers will live as sibling `QQuickItem`s in this single fixed window.** No inter-window coordination, automatic frame coherence.

2. **Hidden `BuddyWindow(QWidget)` as the logic model.** Avoided refactoring the 1100-line QWidget; instead instantiate it with `WA_DontShowOnScreen` and override `paintEvent` to a no-op. The Quick path reads `_render_art_pixmap()`, `_lerped_state()`, `_com_art()`, `_com_widget`, `_sim`, etc. directly. Phase 5 will extract a real `BuddyCore` (no QWidget) when retiring the QWidget path.

3. **Phase-locked physics to vsync via `frameSwapped`.** A separate QTimer beat against `FIXED_DT_S = 4.166 ms` (Win11 PreciseTimer fires at 3-7 ms, FIXED_DT is 4.166 — about 1 in 30 frames no step drained, same θ painted twice). Driving `model._on_tick()` from `frameSwapped` gives exactly one physics step per vsync, alpha pinned at 1.0, no duplicate frames. A 16 ms QTimer kicks `buddy_item.update()` as a fallback heartbeat for when the buddy is settled and no `frameSwapped` would otherwise fire.

4. **θ alpha clamped to [0, 1] in the Quick path's lerp.** The model's `_lerped_state` extrapolates θ past α=1 ("graceful pump-stall recovery"). With vsync paints landing between model ticks, extrapolation oscillates against the next pump's actual physics state and back-steps. Clamping fixes this; we provide our own `_clamped_lerp` instead of using `model._lerped_state` directly.

5. **Click-through via `WS_EX_TRANSPARENT` toggle + `SWP_FRAMECHANGED`.** Phase 1 finding carried in.

**Profile vs Phase A baseline (in motion, drag-and-fling):**

| metric | QWidget path (Phase A) | Quick path (Phase 2) |
|---|---|---|
| body p50 | 10-11 ms | **0.1 ms** |
| body p99 | 13-35 ms | 0.3 ms |
| FPS (cursor over buddy) | 70-80 | **240** |
| FPS (cursor over transparent area) | 70-80 | ~140 (Win throttles `WS_EX_TRANSPARENT` windows) |

All Phase 2 targets met. Phase A invariants preserved: no ghost, drag/fling works, hit-test honors per-pixel alpha.

**Open follow-ups for Phase 3+:**

- Multi-monitor: window currently spans only the primary screen. Phase 4 needs virtual-desktop bounds (sum of all `QScreen.geometry()`) or per-screen reparenting when buddy crosses a screen edge.
- `WS_EX_TRANSPARENT` throttle (~140 fps when cursor is off-buddy): consider a global mouse hook + always-`TRANSPARENT` window so we can keep 240 fps everywhere.
- Followers (Phase 3) become much simpler in this architecture — they're sibling `QQuickItem`s of `buddy_item` inside the same pivot/scene graph.
- BuddyCore extraction (Phase 5).

---

### Phase 2 — Buddy port (original plan, reference)

Port `BuddyWindow` to a `QQuickItem`:
- Master sprite → `QSGSimpleTextureNode` populated from the existing `_render_art_pixmap()` cache
- Lerp + paint clock survive unchanged (`_lerped_state()` is reusable)
- `transform` on the item: translate-to-com → rotate(theta) → translate(-com_art)
- Hit testing: invert the same transform; reuse `is_painted_cell_at` / `_invert_widget_to_art` math
- Wire `frameSwapped` signal to the new paint clock so `_paint_target_ts` becomes the actual next-vsync timestamp instead of `now + 1/refresh_rate`
- Preserve `position_changed` semantics (or its equivalent) so weather + chat dock follower still get notified
- Keep the QWidget physics tick driving sim; just swap the rendering surface

Profile against Phase A on the same scene. Validate: body p50 ≤ 4 ms in motion, 240 fps sustained. If not, debug in this phase before adding followers.

### Phase 3 — Followers — ✅ DONE 2026-04-28

`tokenpal/ui/quick/{bubble_item,dock_mock_item,grip_item}.py` ship the three followers as `QQuickItem` children of the buddy pivot. Validated via `tests/manual/quick_followers.py` on the Windows dev box (RTX 4070, 4K @ 240 Hz): scene-graph composite at 240 Hz with all four items, `buddy.paint` p50 still ~0.015 ms (no regression vs. Phase 2's solo-buddy baseline of 0.1 ms).

**Final architecture (departures from the original sketch):**

1. **Followers are siblings of `buddy_item`, all parented to the pivot.** The original sketch said "child `QQuickItem`s of the same `QQuickWindow`" — but parenting them to the pivot directly (which is itself a child of `contentItem`) means the pivot's `setRotation(degrees(theta))` propagates to all followers automatically. No per-frame `set_pose(anchor_world, angle_rad)` round-trip per follower; the body-aligned offset (`_body_aligned_offset` in the QWidget overlay) falls out of the pivot transform for free.

2. **Anchors are pivot-local constants, not world coords.** Each follower's anchor in pivot-local space depends only on art geometry:
   - bubble tail at `(art_w/2 - com_x_art, -com_y_art - 16)` — top-center of art, plus hover offset
   - dock_mock top-center at `(art_w/2 - com_x_art, art_h - com_y_art + 4)` — under the feet
   - grip bottom-right at `(art_w - com_x_art, art_h - com_y_art)` — corner of art frame
   `_sync_geometry` calls `set_anchor_in_parent(...)` once per frame; positions only change when art geometry changes, so it's a no-op the rest of the time.

3. **Render path: QImage cache → QSGTexture → QSGSimpleTextureNode.** Each item renders its content (rounded rect + text for bubble, snapshot pixmap for dock_mock, alpha-1 hit rect + dot pattern for grip) into a `QImage` on the GUI thread, then `updatePaintNode` uploads it to a `QSGTexture` on the render thread the first frame after invalidation. Cache invalidates only on content change (typing reveal, font/color change, dock snapshot replacement) — not on rotation, position, or vsync. `QQuickPaintedItem` was disqualified by the plan and not used.

4. **Click-through probe extends to bubble + grip.** `BuddyQuickWindow._opaque_probe` now tests the buddy item, then the bubble (if visible), then the grip (if visible). Each item's `contains()` is item-local AABB — sufficient because the bubble's rounded corners are tiny and the grip's hit area is the full square (alpha=1 trick from QWidget `_chrome.py` carried over). The dock mock never accepts mouse events (`AcceptedMouseButtons.NoButton`), matching the QWidget mock's `WA_TransparentForMouseEvents`.

5. **No per-frame paint cost from idle followers.** With the bubble hidden and the dock mock visible but its texture cached, sustained-motion `buddy.paint` p50 is unchanged from Phase 2. The scene-graph composite handles the static texture quads cheaply.

**Open follow-ups for Phase 4+:**

- Backend dispatch (`[ui] backend = "qt" | "quick"` config) — Phase 4.
- Cross-platform validation of the Quick stack on macOS M-series + Linux KDE/X11.
- BuddyCore extraction (Phase 5) — strip the hidden `WA_DontShowOnScreen` BuddyWindow once parity is proven and the QWidget path can be retired.

### Phase 3 — Followers (original plan, reference)

Port speech bubble, dock_mock, grip as child `QQuickItem`s of the same `QQuickWindow`:
- Each item lives in the same scene graph → one paint per frame for all four → no inter-window present desync (the problem A1, A4, and the explicit-clear fix all addressed at the QWidget layer disappears structurally here)
- Rotation around per-item anchors via per-item transforms
- Hit testing per-item via per-pixel alpha
- The buddy's `position_changed` signal becomes a `Q_PROPERTY` on the buddy item that follower items bind to via QML (or signal/slot if all-Python)

### Phase 4 — Backend dispatch — ✅ DONE 2026-04-28 (Windows; cross-platform smoke deferred)

`[ui] backend = "qt" | "quick"` config (default `"qt"`). `tokenpal/ui/qt/overlay.py` branches construction in `setup()` — when `quick`, instantiates `BuddyQuickWindow` and uses `bqw.model` / `bqw.bubble_item` / `bqw.dock_mock_item` / `bqw.grip_item` in the same `self._buddy` / `_bubble` / `_dock_mock` / `_resize_grip` slots the QWidget path used. Both backends launch + run + tear down cleanly via `tests/manual/quick_backend_smoke.py`.

**Final architecture (departures from the original sketch):**

1. **Single QtOverlay class with branched construction, not separate overlay classes.** A `QuickOverlay(QtOverlay)` subclass would have re-implemented ~45 call sites for follower interactions; instead the Quick items duck-type to the QWidget follower API (added `show()` / `hide()` / `set_pose(...)` no-op / `close()` shims on `BubbleQuickItem`, `DockMockQuickItem`, `GripQuickItem`). Every `self._bubble.*`, `self._dock_mock.*`, `self._resize_grip.*` call site stays untouched. Diff against `overlay.py` is constructor-only plus three guards on macOS chrome helpers.

2. **`self._buddy_host` separates "the model" from "the visible host."** On the QWidget backend `_buddy_host is _buddy` (same QWidget). On the Quick backend `_buddy_host` is the `BuddyQuickWindow` (QQuickWindow) and `_buddy` is its hidden model (a `BuddyWindow` with `WA_DontShowOnScreen`). Show/hide is routed through `_buddy_host`; everything else (`position_changed`, `head_world_position`, `body_angle`, `set_right_click_handler`, …) goes through `_buddy` and works identically on both paths.

3. **macOS chrome helpers guarded on `_use_quick_backend`.** `lock_macos_child_above(buddy, grip)` and `apply_macos_stay_visible(grip)` are skipped on the Quick backend because the grip is now a `QQuickItem` inside the buddy's NSWindow rather than a separate top-level NSWindow — z-order and stay-visible inherit. `apply_macos_stay_visible(self._buddy_host)` works unchanged because `QQuickWindow` has `winId()`.

4. **Lazy import of `tokenpal.ui.quick`.** The `BuddyQuickWindow` import lives inside the `if self._use_quick_backend` branch, so QtQuick stays unloaded for users on the QWidget backend.

**Open follow-ups for Phase 5+:**

- Cross-platform validation (macOS M-series, Linux KDE/Wayland, Linux X11). Phase 5 retirement is gated on this; until then the QWidget backend remains the safe default.
- BuddyCore extraction — strip the hidden `WA_DontShowOnScreen` BuddyWindow once parity is proven.
- Doc note in `docs/claude/ui.md` describing the Quick path + `frameSwapped` clock + how to add new follower items.

### Phase 4 — Backend dispatch (original plan, reference)

`[ui] backend` config option. `tokenpal/ui/qt/overlay.py` instantiates the Quick stack when set, falls back to QWidget otherwise. Cross-platform smoke on:
- Windows 11 (dev box) — primary target
- macOS M-series — Metal + NSWindow stay-visible
- Linux KDE / Wayland — KWin known to support translucent QQuickWindow
- Linux X11 — straightforward

Document any platform-specific quirks in `docs/claude/ui.md`.

### Phase 5 — Default flip + retire

Once parity is proven across all four target machines:
1. Flip default to `[ui] backend = "quick"`
2. One release with both backends available
3. Delete the QWidget buddy / bubble / dock_mock / grip code paths
4. Update docs

If a platform regression surfaces in step 1, the QWidget backend remains selectable via config.

## Failure modes to anticipate

- **Translucent QQuickWindow on Windows requires `setColor(Qt.transparent)` AND `setFlag(Qt.WindowTransparentForInput)` is the wrong knob** — that disables ALL input. Click-through-on-transparent-pixels needs per-item per-pixel alpha hit testing or a region from the painted shape. Research notes this works via DirectComposition with `setColor(Qt.transparent)` alone on Win10+/Qt6.
- **`updatePaintNode` runs on the render thread, not the GUI thread.** Guard any access to QWidget-side state. Reads of physics state must be lock-free (the existing lerp values are immutable per pump — this is fine if we copy them at signal time).
- **Custom QSG nodes outlive QQuickItems via `QQuickItem::ItemHasContents` flag.** Forget the flag, get nothing painted. Common gotcha.
- **`QQuickPaintedItem` is QPainter-on-FBO** — defeats the purpose. Use `QQuickItem` + `QSGGeometryNode` / `QSGSimpleTextureNode` for textured-quad rendering.
- **MoltenVK on macOS through Qt RHI has fewer real-world miles than the D3D11 path.** Validate Phase 1 spike on the M-series box specifically before committing further.
- **GNOME-Mutter Wayland still has no `wlr-layer-shell`.** Same yellow cell as today's QWidget path; not a regression, but document.
- **Hit testing on rotated items**: `QQuickItem.contains()` is in item-local coordinates; we need to invert the transform to map back. Reuse `_invert_widget_to_art` math; just swap the coordinate-space adapters.
- **Physics tick still on QTimer 6 ms.** That's not changing. The win is decoupling paint from pump — paints happen at vsync rate (240 Hz) regardless of pump (~166 Hz max), driven by `frameSwapped`.
- **Master sprite cache is keyed on `(lines, font_family)`.** The Quick path needs the same QImage → QSGTexture conversion. Cache QSGTextures separately so we don't rebuild the texture every frame; bind to the existing pixmap cache invalidation.
- **Voice frame swap**: when the buddy art changes mid-conversation, the texture has to invalidate atomically. `QQuickItem::update()` handles the schedule, but the cache must rebuild on the render thread, not in `set_frame`.
- **Drag/fling/zoom**: input events on `QQuickItem` come through `mousePressEvent`/`mouseMoveEvent` similar to QWidget but with item-local coords. Adapter is small but real.
- **Multi-monitor**: `QQuickWindow.screen()` returns the current screen; per-screen DPR handling differs slightly from QWidget. Verify on a multi-monitor setup before Phase 5.
- **Widget-side dialogs (voice modal, chat history) opening from the Quick window**: the parent should still be the QQuickWindow, but Qt's `QApplication`-level focus handling needs `WA_ShowWithoutActivating` on the child QWidget. Same trap as today, slightly different shape.
- **The `_chrome.py` BuddyResizeGrip's "alpha=1 fillRect" trick** for full-rect click-through-as-clickable-when-painted relies on Windows layered-window per-pixel alpha hit-test. Quick's hit-test is its own; the trick may need a different shape (e.g., explicit `acceptedMouseButtons` + `containsMouse` on a transparent-but-clickable item).
- **Phase 5 retirement risk**: if a real-world regression surfaces months later, deleting QWidget code is hard to undo. Keep one release window before the delete.

## Done criteria

- Single `QQuickWindow` on screen with buddy + 3 follower items as children
- Tick body p50 ≤ 4 ms at 4K @ 240 Hz with sustained motion (the pre-migration baseline was 10–11 ms)
- 240 fps sustained during a 30-second drag-and-fling stress test (current: ~70–80 fps in motion)
- Paint clock fed by `frameSwapped`, replacing the `now + 1/refresh` approximation
- All Phase A invariants preserved: ghost stays gone, followers paint coherently per frame, weather follower still tracks
- All current features preserved: per-pixel transparency, click-through, drag/fling/zoom, voice frame swap, multi-monitor, edge-dock, offscreen rescue, tray, voice modal, chat history, news window
- Cross-platform smoke pass on Windows 11, macOS M-series, Linux Wayland-KDE, Linux X11 — if a platform regresses, called out explicitly
- All 246+ qt/buddy/paint/physics tests still green
- New tests: `tests/test_quick/test_buddy_item.py` and `tests/test_quick/test_window_translucency.py` cover the Quick path
- Ruler-scroll smoke test (`tests/manual/ruler_scroll.py`) passes the iPhone slo-mo gate on the dev panel
- Backend dispatch via `[ui] backend = "qt" | "quick"` config; both work for at least one release before QWidget retirement
- Doc note in `docs/claude/ui.md` explaining the Quick path, the `frameSwapped`-driven clock, and how to add new followers as `QQuickItem`s

## Parking lot

- **Multi-monitor with mixed DPRs (Quick path)** — naive fix is to size the `QQuickWindow` to `virtualGeometry()` (union of all screens). Works fine when every screen has the same DPR; on mixed-DPR setups (e.g., 4K @ 200% primary + 1440p @ 100% secondary) Qt's per-monitor DPI handling against a single DirectComposition surface produces a disjointed / double-composite render on the off-DPR screen (verified on dev box 2026-04-28, see commit 837dee7 reverted in commit ?). The right fix is **one QQuickWindow per screen with reparenting on edge cross** — each window has uniform DPR, the scene-graph items move between them as the buddy crosses. Non-trivial: textures and child items need to migrate without flicker, and the click-through toggle has to track the active window. Until that lands, the buddy stays on the primary screen.
- **Offscreen rescue silently disabled on the Quick path** — `BuddyWindow._tick_offscreen_rescue` bails on `not self.isVisible()`, and the hidden model widget is never `.show()`n (`WA_DontShowOnScreen` + no show call). Easy fix: `self._model.show()` in `BuddyQuickWindow.__init__` (with `WA_DontShowOnScreen` set, the show is logical-only and produces no native window). Bundle this with the multi-monitor rework so we test rescue + cross-screen handling together.
- **Speech bubble z-order vs. weather (Quick path)** — bubble is a `QQuickItem` inside the buddy `QQuickWindow`; weather (`SkyWindow` + `BuddyRainOverlay`) is a separate translucent `QWidget`. Both are `WindowStaysOnTopHint`, so when weather is enabled it can land on top of the bubble. Cosmetic, not a daily-driver blocker. Fix: either bring `_buddy_host` to top after weather shows, or reparent weather under the QQuickWindow as additional `QQuickItem`s when on the Quick backend.
- **Off-buddy 240 fps throttle** — Windows throttles `WS_EX_TRANSPARENT` windows to ~140 fps when the cursor isn't over an opaque pixel. Global mouse hook + always-transparent window + manual input injection on hit could keep 240 fps everywhere. Polish.
- **BuddyCore extraction** — strip the hidden `WA_DontShowOnScreen` `BuddyWindow` into a non-`QWidget` core so Phase 5 can delete the QWidget code paths without breaking the model.
