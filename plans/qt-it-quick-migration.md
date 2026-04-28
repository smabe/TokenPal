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

### Phase 1 — Spike (validate the path)

Bare-minimum `QQuickWindow + QQuickItem` rendering the master pixmap as a textured quad. Verify on the dev box (Windows / 4K / 240 Hz):

1. Frameless transparent QQuickWindow shows the master pixmap with full alpha
2. Rotation animates without tearing
3. `frameSwapped` fires at 240 Hz (or whatever `QScreen.refreshRate()` reports)
4. Click-through-on-transparent-pixels works (per-item alpha hit-test)
5. Tick profile: body p50 < 4 ms during forced rotation

If any of these fail, stop and decide before continuing — research called out these specific risks (translucent QQuickWindow on Wayland, GNOME-Mutter, Linux EGL/Vulkan compatibility, MoltenVK on macOS). One day, one file.

### Phase 2 — Buddy port

Port `BuddyWindow` to a `QQuickItem`:
- Master sprite → `QSGSimpleTextureNode` populated from the existing `_render_art_pixmap()` cache
- Lerp + paint clock survive unchanged (`_lerped_state()` is reusable)
- `transform` on the item: translate-to-com → rotate(theta) → translate(-com_art)
- Hit testing: invert the same transform; reuse `is_painted_cell_at` / `_invert_widget_to_art` math
- Wire `frameSwapped` signal to the new paint clock so `_paint_target_ts` becomes the actual next-vsync timestamp instead of `now + 1/refresh_rate`
- Preserve `position_changed` semantics (or its equivalent) so weather + chat dock follower still get notified
- Keep the QWidget physics tick driving sim; just swap the rendering surface

Profile against Phase A on the same scene. Validate: body p50 ≤ 4 ms in motion, 240 fps sustained. If not, debug in this phase before adding followers.

### Phase 3 — Followers

Port speech bubble, dock_mock, grip as child `QQuickItem`s of the same `QQuickWindow`:
- Each item lives in the same scene graph → one paint per frame for all four → no inter-window present desync (the problem A1, A4, and the explicit-clear fix all addressed at the QWidget layer disappears structurally here)
- Rotation around per-item anchors via per-item transforms
- Hit testing per-item via per-pixel alpha
- The buddy's `position_changed` signal becomes a `Q_PROPERTY` on the buddy item that follower items bind to via QML (or signal/slot if all-Python)

### Phase 4 — Backend dispatch

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

(empty at start)
