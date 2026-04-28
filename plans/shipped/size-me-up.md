# size-me-up

## Goal
Two coupled changes: (A) migrate Qt weather sprites (sun, moon, overcast clouds, rain cloud) from in-file duplicates to a `render-to-QPixmap (supersampled 2–3× at build) + drawPixmap with SmoothPixmapTransform` pipeline backed by the Textual-overlay sprites in `tokenpal/ui/ascii_props.py`. (B) add a single zoom factor that wholesale-scales the entire buddy construct (buddy + speech bubble + chat dock + sky window + rain overlay), driven by a drag-resize grip on the buddy. (A) enables (B) — pixmap-based sprites are size-independent of the font cell, so zoom is a pure scale factor on the target rect.

## Architectural decisions (locked in via research)

**Buddy stays on direct paint. Weather goes pixmap+supersample.** Research findings that justify this split:

1. **Qt 6.11 only exposes nearest + bilinear filters** for `drawPixmap`/`drawImage` (`Qt::FastTransformation` and `Qt::SmoothTransformation`, qpaintengine_raster.cpp:799). No bicubic, Lanczos, or trilinear without significant refactor (QOpenGLWidget + manual texture management) or external dep (Pillow at sprite-build only).

2. **Direct-paint `drawText` under rotation re-rasterizes glyph outlines via the rotated matrix** — it does NOT degrade to bitmap rotation. `QFontEngine::supportsTransformation` selects the vector path; stems stay vector-defined at any angle. ClearType subpixel AA is disabled under rotation but grayscale AA stays.

3. **Pixmap path under rotation is bilinear sampling on the source bitmap** — ~1px blur per stem edge at non-axis-aligned angles. Trade-off: pixmap motion is temporally coherent (no shimmer); direct paint can produce visible "crawling pixels" on slowly-changing θ.

4. **The buddy spends most time at θ=0** (settles when idle), so direct paint's sharpness is realized for the dominant case. The shimmer during the brief swing intervals is acceptable.

5. **Pixel-art scalers (xBR, hqx, Scale2x) don't fit our case** — they're tuned for flat-color hard-edge sprites; our `▓░▒` are font-anti-aliased gradients that confuse the edge detection. Integer-only output. Not worth the integration cost.

6. **Free quality upgrade for the pixmap path**: render the source pixmap at 2–3× cell size at sprite-build time, then let bilinear handle runtime scale/rotate. One `QImage.scaled(..., SmoothTransformation)` call per cache build, gives noticeably sharper output than naive native-size bilinear at every target scale.

7. **Lanczos via Pillow is an optional follow-up** if non-native zooms (say 50% or 200%) ever need to look truly pristine. ~30 LOC + dep, gated behind the same cache-build step. Not in v1.

## Progress

Shipped:
- **Phase 0** (commit `d3a4bb1`, simplify `d1f28a5`) — `UiState.zoom: float` + safe migration in `tokenpal/config/ui_state.py`. `_DEFAULT_ZOOM = 1.0` constant, bool rejected from the typecheck. Tests cover default, missing-key, malformed, and round-trip.
- **Phase 1** (commit `4b2164c`, simplify `d1f28a5`) — `render_sprite_pixmap` in `tokenpal/ui/qt/_text_fx.py`. Supersamples `dpr * supersample`, downsamples via `QImage.scaled(SmoothTransformation)`, returns a `QPixmap` with `setDevicePixelRatio(dpr)`. **Note**: this commit also accidentally bundled a pre-existing user WIP move of `paint_block_char` + `_BLOCK_*` constants into `_text_fx.py` (those changes were already in the working tree at session start).
- **Phase 2** (commit `30fcd73`, simplify `2297aae`, fix `e84ff9e`) — Migrated `tokenpal/ui/qt/weather.py` sun/moon/overcast/rain-cloud sprites to cached `drawPixmap` path. Sprites now read from `tokenpal/ui/ascii_props.py`. `SkyWindow.set_zoom(factor)` rescales font + busts the cache. `BuddyRainOverlay.set_zoom(factor)` rescales font (no cache). Cache key is `(sprite.lines, color.rgb(), cell_w, line_h)` — overcast A/B share an entry. Tests pin cache hit / bust / overcast dedup.
- **Phase 3** (commits `d22d668` + `d004ed5`) — `BuddyResizeGrip(QWidget)` in `tokenpal/ui/qt/_chrome.py`. Fixed 16px grip emits per-pixel signed `zoom_drag_delta(int)` on y-drag. Diagonal-dot paint factored into `_paint_diagonal_dots` (also used by `GlassSizeGrip`).
- **Phase 4** (commit `47ce37c`) — `set_zoom(factor)` on `BuddyWindow`, `SpeechBubble`, `ChatDock`. Each tracks `_base_font` + `_zoom`; effective font rebuilt via shared `scale_font` helper in `_text_fx.py`. `BuddyWindow` also recomputes inertia via new `RigidBodySimulator.set_inertia` (frozen-config replace). `_measure_block_paint_width` cached by `(family, pointSize)` so drag-zoom doesn't re-rasterize the probe glyph every tick. Absorbed the pre-existing `buddy_window.py` WIP (`paint_block_char` fallback in `paintEvent`). Tests in `tests/test_qt_set_zoom.py` cover font/cells/inertia rescale, no-op on same factor + invalid factor, and chain-from-base semantics for all three widgets.
- **Phase 5** (commit `e9e5f60`) — `QtOverlay.set_zoom(factor)` orchestrator with `_clamp_zoom` (range [0.5, 2.5], snapped to 4dp to stop drag-arithmetic from churning the pipeline), `_fan_out_zoom` shared between `set_zoom` and setup-time restore, `_on_zoom_drag_delta(dy)` slot wired to a re-emitted `BuddyWindow.zoom_drag_delta` signal. `BuddyResizeGrip` embedded as a child of `BuddyWindow` and parked at the widget's bottom-right in `_refresh_view`. UiState persist callback rewritten to take a full `UiState` dict (no more clobbering zoom on visibility toggle); narrowed in `AbstractOverlay` to `Callable[[UiState], None]`. `_persist_ui_state` now debounces writes with a 250ms QTimer + a `_persist_pending` flag — `flush_pending_persist()` forces a synchronous flush, called from `teardown()` and from tests. Restored zoom from `UiState.zoom` is applied via `_fan_out_zoom` on first `setup()` before initial show. Tests cover clamp, fan-out, persist-includes-zoom on visibility toggle, restore-applies-on-setup, and grip→overlay signal propagation.
- **Phase 6 (automated verification)** — `ruff check tokenpal/` clean. `pytest` 1946 passed; 9 failures are all pre-existing and unrelated to size-me-up. `mypy tokenpal/` shows 12 pre-existing errors in 5 untouched files; targeted mypy on size-me-up's touched modules is clean.
- **Phase 6.5 (manual-smoke fixes)** — three issues surfaced when the user actually exercised the drag-grip; all fixed before ship:
  - **Physics scaling** (`fabc526`, gravity-tune `01ac79d`) — phase 4 only scaled inertia; gravity, max_linear_speed, upright_bias (z²), upright_bias_radius, and settle thresholds all need to scale too or feel diverges with size. New `_zoomed_physics_config()` builder owns the dimensional-analysis exponents; `RigidBodySimulator.set_inertia` → `set_config` since BuddyWindow now owns the full config build. Base gravity bumped 6000 → 12000 for the right 1× swing speed under the new model.
  - **BuddyResizeGrip "single point" hit area** (`5643491` after grip-size bumps `45aff43`/`5643491`) — Windows layered windows (`WA_TranslucentBackground`) hit-test by per-pixel alpha. The grip's un-painted pixels were alpha=0 → click-through, so the widget-size knob was a red herring. Fix: paint a near-zero-alpha (`QColor(0,0,0,1)`) background across the full widget rect. Widget size still determines hit area; alpha-paint just makes the OS register it.
  - **Sun horizontally stretched + clipped** (`6bcda2b`) — `SkyWindow` had the same `self.fontMetrics()` vs `QFontMetrics(self._font)` bug I fixed in BuddyWindow during phase 4: cell_w scaled with self._font, line_h was stuck at the widget's inherited-font ascent. Fixed for sky too. Sky panel was hard-coded to 200×120 px → 2× sun overflowed; now resizes proportionally on `set_zoom`.
  - **Chat dock pill flattened at small zoom** (`04efa79`) — input pill border-radius was hard-coded at 14 px; at 0.5× zoom Qt clamped to nearly-flat corners. Now derived from scaled height (`scaled_input_height // 2`); stylesheet re-applied on every `set_zoom`.
- Manual smoke confirmed by user across 0.5× / 1× / 2× drags. Plan shipped.

Pre-existing user WIP still uncommitted (out of size-me-up's scope):
- `tokenpal/ui/ascii_props.py` — sun/moon sprites tweaked with `▀`/`▄` rounded ends. Decorative; ship as a standalone commit when convenient.

## Non-goals
- Per-widget independent resize (e.g. resizing only the buddy without resizing the bubble). Wholesale only.
- Reworking the buddy's direct-paint pipeline (`paintEvent` keeps `paint_block_char` + `drawText`). Buddy stays sharp; only weather sprites go through pixmap.
- Re-authoring sprite art. Use what's in `ascii_props.py` as-is. Tweaks to the moon's rounded ends already shipped on the Qt-only `MOON_LINES`; for migration we accept the props.py shape and lose those edits unless we mirror them in props.py first (see parking lot).
- Adding a separate "weather zoom" or "buddy zoom" — single zoom controls everything.
- Resizing the chat history / news history windows from the same handle. Those keep their own `GlassSizeGrip` (already shipped from `fix-it-felix`).
- Replacing `WeatherSim` particle physics. Spawn rates may need cell-size adjustment but the sim model stays.
- Animated zoom transitions. Live drag updates only — no tweened in/out.
- Touching the Textual overlay path (`tokenpal/ui/buddy_environment.py`). It already renders ascii_props.py via the terminal.

## Files to touch
- `tokenpal/ui/qt/_text_fx.py` — add `render_sprite_pixmap(lines: tuple[str, ...], color: QColor, cell_w: int, line_h: int, dpr: float, supersample: int = 2) -> QPixmap`. Renders the sprite via existing `paint_block_char` + `drawText` fallback into an offscreen `QImage` at `cell_w * dpr * supersample × line_h * dpr * supersample` per cell, then calls `QImage.scaled(target_size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)` to downsample by `supersample` factor (gives bilinear-from-supersampled output → noticeably sharper than rendering at native cell size). Sets `pixmap.setDevicePixelRatio(dpr)` on the final pixmap so subsequent `drawPixmap` interprets it as HiDPI. Default `supersample=2` is the cheap free quality bump; callers can pass 3 for extra sharpness if perf allows.
- `tokenpal/ui/qt/weather.py` —
  (a) Delete the local sprite duplicates (`SUN_LINES`, `MOON_LINES`, `_OVERCAST_CLOUD_LINES`, `RAIN_CLOUD_LINES`). Import the props sprites from `tokenpal/ui/ascii_props.py`.
  (b) Add a per-`SkyWindow`/`BuddyRainOverlay` pixmap cache: `dict[tuple[int, str, int, int], QPixmap]` keyed on `(id(sprite_lines), color_hex, cell_w, line_h)`. Invalidate on font/zoom change.
  (c) Rewrite `_paint_celestial`, `_paint_clouds`, `_draw_sprite` so the sprite path becomes `painter.drawPixmap(target_rect, cached, source_rect)` with `SmoothPixmapTransform` render hint. Keep drift / scale / lightning paths intact — only the sprite rasterization changes.
  (d) Wire a `set_zoom(factor: float)` method that recomputes `cell_w`/`line_h` (via font size) and busts the pixmap cache.
- `tokenpal/ui/qt/buddy_window.py` —
  (a) Accept zoom in construction or via `set_zoom(factor)`. Scale `font_size` by zoom, then call `_recompute_geometry()` which rederives `cell_w`, `line_h`, `art_w`, `art_h`. Recompute physics inertia: `_compute_inertia` is size² in `art_w*art_h`, so it auto-scales when those grow — confirm with test.
  (b) Add a `BuddyResizeGrip` child widget anchored bottom-right of the buddy widget rect. Must be input-active even though the buddy uses `setMask` for click-through outside painted glyphs — extend the mask region to include the grip rect.
  (c) The grip must NOT rotate with the buddy. Easiest: render in widget-local space without applying `_build_transform()`. Simpler than a separate top-level window.
  (d) Drag math: vertical drag distance maps to a zoom delta; emit `zoom_drag_delta` signal that `QtOverlay` integrates into a clamped zoom factor and fans out via `set_zoom`.
- `tokenpal/ui/qt/_chrome.py` — add `BuddyResizeGrip(QWidget)` with the diagonal-dot paint matching `GlassSizeGrip` aesthetic. Different from `GlassSizeGrip` (which subclasses `QSizeGrip` and uses `startSystemResize`) — this one emits a custom signal because we want a zoom delta, not a window resize.
- `tokenpal/ui/qt/overlay.py` —
  (a) `QtOverlay.set_zoom(factor: float)` orchestrator. Clamp to `[ZOOM_MIN, ZOOM_MAX]` (proposed: 0.5–2.5). Fan out to buddy, bubble, dock, sky window, rain overlay, then call reanchor for each.
  (b) Wire the buddy's `BuddyResizeGrip` signal to `set_zoom`.
  (c) On startup, restore zoom from `UiState.zoom` (default 1.0) before the first `show()` so initial layout is correct.
  (d) On every successful zoom change, call `_persist_ui_state()` (which already exists for window visibility).
- `tokenpal/ui/qt/speech_bubble.py` — accept `set_zoom(factor)`. Scale `font_size` by zoom and rebuild the box. Existing `apply_font(QFont)` path is the right hook.
- `tokenpal/ui/qt/chat_window.py` (ChatDock only — NOT `ChatHistoryWindow` since log windows are non-goal) — accept `set_zoom(factor)`. Scale the dock's font + the fixed `_DOCK_DEFAULT_WIDTH = 360` by zoom.
- `tokenpal/config/ui_state.py` —
  (a) Extend `UiState` TypedDict with `zoom: float`.
  (b) `_default_state()` returns `1.0`.
  (c) `load_ui_state` reads `zoom` from disk with safe fallback to 1.0 if missing/malformed.
  (d) `save_ui_state` persists zoom in the JSON.
- `tests/test_ui_state.py` — coverage for zoom default, round-trip, missing-key migration, malformed value.
- `tests/test_qt_overlay.py` — assert `set_zoom` fans out (mock buddy/bubble/dock/sky received the call) and `_persist_ui_state` is called.
- `tests/test_qt_weather.py` — assert pixmap cache hit on second paint, cache bust on zoom change.

## Failure modes to anticipate
- **Physics calibration breaks at non-1.0 zoom.** `RigidBodyConfig` defaults (`spring_k=180.0`, `damping=12.0`, `mass=1.0`, `inertia=4000.0`) are calibrated for the buddy at his current size. Inertia auto-scales because `_compute_inertia` is mass × art_w² + art_h². Spring force scales with displacement (px), but at higher zoom the displacement-to-perceived-distance ratio changes — buddy may swing faster or slower than expected. **Mitigation**: hold spring_k/damping constant for now, ship, then tune via observation. If swing feels wrong, scale spring_k by zoom and damping by sqrt(zoom) (critical-damping math). Don't over-engineer the first cut.
- **Pixmap DPR mishandling.** If we render at 1× resolution and let bilinear stretch on a 2× display, we get blur. If we render at 2× and don't call `setDevicePixelRatio(dpr)`, `drawPixmap` paints it twice as large as intended. Mitigation: render source at `cell × dpr × supersample`, downsample by `supersample` via `QImage.scaled(SmoothTransformation)`, then call `pixmap.setDevicePixelRatio(dpr)` on the result. Verify on user's actual display (devicePixelRatio=2.0) at phase 2 close.
- **Supersample × DPR explosion.** At DPR=2 and supersample=2, source bitmap is rendered at 4× cell-pixel area. For a 15×8 moon sprite at 14pt cell (~14px wide), that's 14·15·4 × 14·8·4 = 840×448 px source. Fine for individual sprites, but if cache balloons or supersample creeps to 3 (12× area), worth measuring. Cache one pixmap per (sprite, color, cell_size) — six entries max for the Qt overlay. Not a memory concern; a perf concern only on cache-build, which is rare.
- **Bilinear soft halo on translucent surfaces.** Sprite edges become semi-transparent at 60% scale, compose against desktop wallpaper. Acceptable per prior discussion (translucent UI already has soft compositing) but worth confirming visually before phase 2 ships.
- **Click-through region vs grip.** Buddy uses `setMask(QRegion(self.rect()))` to make the entire widget clickable in the bounding box (per `_update_click_mask`). For grip to work, ensure the mask includes the grip's bounding rect. Buddy's "click-through outside painted glyphs" is more nuanced than I initially flagged — re-verify what the current mask actually does before assuming.
- **Grip rotates with buddy.** `_build_transform()` applies rotation around COM in `paintEvent`; a child widget painted in widget-local coordinates (no transform) won't rotate, which is desired. But hit-testing is in widget coords too — that should be fine. Verify with a tilted buddy.
- **Drag direction sensitivity.** Vertical drag = zoom is conventional but vertical drag from bottom-right of a buddy that swings on physics is awkward. Consider diagonal drag (down-right = bigger, up-left = smaller) or simply scroll-wheel as the primary interaction with drag as fallback. Decide in phase 5.
- **Zoom < buddy_min_grip_size makes the grip ungrabbable.** At 0.5× zoom the grip is tiny. Mitigation: clamp grip pixel size with a floor (e.g. min 16px regardless of zoom). The grip lives in widget-local coords, so its size can be independent of buddy zoom.
- **Zoom > screen pushes bubble/dock offscreen.** ChatDock is anchored below the buddy; at 2.5× zoom the dock's bottom edge may exceed screen height. Mitigation: existing `offscreen_rescue_target` for the buddy should naturally pull the whole construct back. Verify after fan-out.
- **Cache key collision across sprites with same content but different drift state.** OVERCAST_CLOUD_A and OVERCAST_CLOUD_B share `_OVERCAST_CLOUD_LINES`. Cache is keyed on `id(sprite_lines)` so they hit the same cache entry — fine, drift is applied as `translate()` at paint time. Important: do NOT bake drift into the cache key.
- **Sprite layout assumption breakage.** weather.py computes `width_px = len(sprite[0]) * self._cell_w` and positions sprites top-right. Bigger props sprites are wider than the Qt-local copies — check that the props moon (15 wide) fits in `SkyWindow`'s width. If not, scale the target rect down further or shrink the SkyWindow.
- **Pixmap rendering of `░ ▒` at small target sizes.** Bilinear blurs the dotty Consolas glyphs into smooth fades — that's the win. But at very small zoom (<0.5×), too much information is lost; fade may pixelate or alias. Eyeball at min zoom; raise floor if needed.
- **Speech bubble vs ChatDock font conflict.** Bubble uses its own font_size (13), dock uses `_CHAT_FONT_DEFAULT_SIZE = 13`. Both are independently configurable today. When zoom multiplies them, multiply the user's configured size, not a hardcoded default — read from FontConfig where available.
- **UiState migration for installed users.** Existing `~/.tokenpal/ui_state.json` won't have a `zoom` key. The loader must default to 1.0 silently; do not error on missing key.
- **Drag throttling.** Live drag emits high-frequency mouse-move events; each one triggers `set_zoom` → fan-out → reanchor → repaint of every translucent widget. May pin CPU. Mitigation: throttle to 30Hz or only fire on min delta (e.g. 2px change).
- **Tests for `setMask` and grip hit-testing.** No existing tests cover the buddy's mask or click-through behavior explicitly. Phase 5 adds the first.

## Done criteria
- `weather.py` no longer defines `SUN_LINES`, `MOON_LINES`, `_OVERCAST_CLOUD_LINES`, `RAIN_CLOUD_LINES`. All sprite data comes from `tokenpal/ui/ascii_props.py` via import.
- Sun, moon, overcast clouds, rain cloud render via `painter.drawPixmap(...)` with `SmoothPixmapTransform`. Source pixmaps are rendered at 2× supersample then downsampled via `QImage.scaled(SmoothTransformation)` at cache-build time. Pixmap cache hits on repeated paint of the same sprite + color + cell size. User confirms moon/sun look smooth (no harsh `░ ▒` dot grid, no half-sharp/half-dotty mismatch, no soft halo on edges).
- A `zoom` field exists in `UiState`, defaults to 1.0, persists across restarts, migrates cleanly for users without it.
- The buddy has a visible, draggable resize grip on his bottom-right that doesn't rotate with him and is grabbable at any zoom level.
- Dragging the grip wholesale-scales buddy + bubble + dock + sky + rain. They stay anchored relative to one another.
- At 0.5×, 1.0×, and 2.0× zoom: visual layout looks correct, no widget falls offscreen, buddy physics still feels alive (swing, drag, edge-dock all work). User confirms by exercising each interaction.
- `pytest`, `ruff check tokenpal/`, `mypy tokenpal/ --ignore-missing-imports` all green.

## Parking lot
- **Lanczos at sprite-build time** if non-native zooms (50%, 200%, etc.) need to look pristine. Add Pillow as optional dep, route the cache-build through `Image.LANCZOS` instead of Qt's `SmoothTransformation`. ~30 LOC. Skip unless v1 supersample+bilinear is visibly insufficient.
- **Mirror the moon's `▀`/`▄` rounded ends from MOON_LINES into ascii_props.py's MOON_SPRITE** if v1 ships and the user wants the rounded look back on the migrated path. Currently the rounding edits are in the to-be-deleted `MOON_LINES` only.
- **Buddy → pixmap path** if shimmer-during-swing turns out to be objectionable. Hybrid path: pixmap when `|ω| > threshold`, direct paint at rest. Don't do this preemptively.
- **Spring/damping calibration for non-1.0 zoom** if buddy swing feels off at min/max zoom. Scale `spring_k` linearly by zoom and `damping` by `sqrt(zoom)` per critical-damping math. Defer until after v1 ships and user reports.
