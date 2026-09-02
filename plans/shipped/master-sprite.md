# master-sprite

## Context

The buddy is now a per-content cached pixmap blitted through a rotation transform — much closer to a game sprite than the per-glyph paint loop we started with. Cache hit rate during animation is good; frame switches mid-fling are now instant.

But two issues remain, and they're the same issue:

1. **Zoom re-renders every pose.** Cache key is `(tuple(lines), font_family, font_pointSize)`. Every `set_zoom` changes `font_pointSize` → every pose's cache entry misses → ~5-15 ms rebuild per pose at the new zoom. The user observed this and called it out: "scaling should not redraw anything."

2. **Residual rotation jitter.** Each pose has N copies in cache (one per zoom level visited). The bitmap *for a given zoom* is invariant across rotation, but the source isn't truly invariant across the buddy's lifetime — it's regenerated every time the user zooms.

A real pixel game has *one* master texture per sprite, sampled through a transform matrix that handles zoom + rotation simultaneously. That's the gap to close.

## Goal

One invariant master sprite per pose, rendered once at fixed reference resolution. Zoom and rotation both become parameters of the same `setWorldTransform`/`drawPixmap` call. No re-rasterization on zoom. No `font_pointSize` in the cache key. Render path is a single bilinear texture sample, like a game engine.

## Non-goals

- Mip-mapped multi-tier masters. Only consider if 3×+ zoom looks too blurry in practice.
- GPU rendering via `QtQuick` / `QOpenGLWidget`. Loses `WA_TranslucentBackground`; not worth the rewrite.
- Touching anything outside `tokenpal/ui/qt/buddy_window.py`.
- Reworking the Fix-Your-Timestep accumulator, θ interpolation, or AABB slack — all already in place and load-bearing for smoothness. Leave them alone.

## Files to touch

- `tokenpal/ui/qt/buddy_window.py` — only file.

## Approach

### 1. Master font + metrics (one-time, at `__init__`)

Add three new fields, set up alongside the existing `_base_font` / `_font` setup:
- `_master_font`: `scale_font(self._base_font, _MASTER_ZOOM)` where `_MASTER_ZOOM = 2.0` (see decision below)
- `_cell_w_master`: `_measure_block_paint_width(_master_font) - 1` (mirror of `_font_init_metrics`)
- `_line_h_master`: `QFontMetrics(_master_font).height()`

These never change after `__init__`. Independent of `set_zoom`.

### 2. Cache key

Change from `(tuple(lines), font_family, font_pointSize)` to `(tuple(lines), font_family)`. Master is at fixed reference, so font size drops out. All zooms share the same cache entry per pose.

### 3. `_render_art_pixmap` rasterizer

- Use `_master_font` for `painter.setFont` and `QFontMetrics`.
- Use `_cell_w_master` and `_line_h_master` for cell layout (replaces `cell_w = self._cell_w` and `line_h = self._line_h` inside the rasterizer only).
- Compute `total_w = cols * _cell_w_master`, `phys_w = cols * _cell_w_master * scale`, `phys_h = rows * _line_h_master * scale`.
- Set `pixmap.setDevicePixelRatio(dpr * supersample)` so the master's logical size = `(cols * _cell_w_master, rows * _line_h_master)` — fixed in master units, independent of zoom.
- `y_stretch = _line_h_master / max(QFontMetrics(_master_font).ascent(), 1)` — computed from master metrics.

The cache lookup happens before all this; only the *miss* path goes through the rasterizer.

### 4. `paintEvent`

Already correct in shape. The destination rect `QRect(0, 0, self._art_w, self._art_h)` is in **zoomed art coords**; the master pixmap's logical size is in **master art coords**. `drawPixmap` applies the implicit scale = (current_cell_w / master_cell_w). Combined with the rotation transform, this is a single bilinear sample that does scale + rotate at once — exactly like a sprite quad in a game engine.

No code change needed in `paintEvent` itself; the change in `_render_art_pixmap` (different logical-size pixmap) makes `drawPixmap` start scaling.

### 5. `set_zoom`

- Update `self._font` ✓ already done
- Recompute `self._cell_w`, `self._line_h` via `_measure_cells` → `_font_init_metrics` ✓ already done
- Update physics config ✓ already done
- **Do not invalidate `_pixmap_cache`** — masters are zoom-independent now
- `_master_font`, `_cell_w_master`, `_line_h_master` are NOT touched

### 6. `_measure_cells`

Keep as-is. Still sets `self._art_w` / `self._art_h` (zoomed coords, drives AABB and transform). Still sets `_art_pixmap = None` (forces re-lookup of the active cache reference on next paint). Note that `_art_pixmap = None` does NOT clear `_pixmap_cache` — only the active pointer.

### 7. `_measure_block_paint_width` cache

Already keyed by `(family, pointSize)`. Master font has a different point size from the zoomed font, so a single fresh measurement happens at `__init__` and is reused forever. No change needed.

## Decisions

### Reference zoom factor

Recommend **`_MASTER_ZOOM = 2.0`**. Trade-offs:
| Reference | Master memory per pose | Crisp range | Notes |
|-----------|------------------------|-------------|-------|
| 1.5 | ~1.9 MB | 0.5×–1.5× | Tighter RAM, blurry past 1.5× |
| **2.0** | **~3.4 MB** | **0.5×–2.0×** | **Balanced (recommended)** |
| 3.0 | ~7.7 MB | 0.5×–3.0× | Crisp at all zooms, 2.25× more RAM |

Per-pose figures assume ~30 cols × 15 rows × cell_w ≈ 7-14 px × supersample 2. ~10 poses cached for the active voice → 30–80 MB total. If the active voice has unusually large art, consider 1.5 reference.

### Supersample factor

Keep `supersample = 2`. Already in place; matches the resolution budget of today's per-zoom cache at zoom = 1. Bump to 3 or 4 only if rotation jitter at high zoom still reads bad after this lands. (Memory grows quadratically.)

## Failure modes to anticipate

- **Master font init order**: `_master_font` must be set up *before* `_render_art_pixmap` is ever called. The first call happens from `paintEvent` after the widget is shown. Init in `__init__` after `_base_font` and before any geometry/widget setup is plenty early. Verify `__init__` doesn't accidentally call `_render_art_pixmap` (it doesn't today).
- **Glyph stretch ratio drift across platforms**: `y_stretch = line_h / ascent` of `_master_font`. Both metrics come from `QFontMetrics` of the same font instance — Windows Consolas vs macOS Menlo will produce different ratios, but that's *correct*: each platform's master gets the right stretch for its font, and zooming preserves that since the master is at fixed reference for that platform.
- **Cell-width drift between master and zoomed font**: `_cell_w` (zoomed) and `_cell_w_master` are computed by the same `_measure_block_paint_width - 1` formula on different font sizes. The ratio `_cell_w / _cell_w_master` should equal `current_zoom / _MASTER_ZOOM` exactly when fonts scale linearly. Any sub-pixel rounding drift becomes a tiny non-uniform scale in `drawPixmap` — invisible in practice, but worth keeping in mind if pixel-perfect alignment matters.
- **AABB at extreme zoom**: AABB is computed from `art_w`, `art_h` (zoomed) — unchanged by this plan, so AABB still fits the painted bitmap at the current zoom. The master pixmap's logical size is irrelevant to AABB. Verify `_recompute_geometry` continues to use zoomed metrics (it should — read `self._art_w/_art_h`, not master).
- **Cache memory unboundedness**: today the cache grows by ~10 entries per zoom level visited. After this plan, ~10 entries total per active voice, period. If the voice changes (different `_base_font` family), a fresh family of entries appears — old voice entries are orphaned. If voice swaps are rare, leave them. If frequent, evict on voice swap (low priority).
- **Mid-flight zoom edge case**: if the user drag-zooms while the buddy is rotating, the cache is now untouched but `art_w`/`art_h` change → `_recompute_geometry` resizes the widget → mask updates. With `widget.move()`/`setMask()` no-op guards already in place this should be smooth, but watch for visible "snap" at the moment zoom changes.

## Verification

1. **Lint, type, tests**: `ruff check tokenpal/ui/qt/buddy_window.py && mypy ... && pytest tests/ -k "qt or buddy or paint or physics"`. Expect 246 passing.
2. **Smoke at zoom = 1.0**: launch, fling, settle. Should look the same as today or slightly cleaner (master at 2× base + 2× supersample = 4× base resolution for the rotation source, vs today's 2× base at zoom 1).
3. **Zoom-stall test**: drag-zoom from 0.5× → 2.5× while the buddy is animating (e.g., talking). The current build stalls 5–15 ms per zoom step (cache rebuild). After this plan, every zoom step should be a cache hit — no stall.
4. **Quality test at extremes**: at zoom 0.5× should be crisp (downsample). At zoom 1.0× crisp. At zoom 2.0× pixel-for-pixel with master (sharpest). At zoom 2.5–3.0× slight bilinear blur on glyphs — acceptable; bump `_MASTER_ZOOM` if not.
5. **Memory check**: launch, zoom through full range, observe `_pixmap_cache` size. Should top out at ~10 entries per voice, ~30–60 MB total. If it keeps growing per zoom, the cache key still has `font_pointSize` — fix.
6. **Rotation jitter check**: fling at zoom 2.0× and 3.0×. Compare to today. The jitter the user described should be reduced (or attributable to irreducible bilinear-rotation sub-pixel sampling, which is the floor for any bitmap-based renderer including game engines).

## Resume context for the new session

These are already in place and load-bearing — **do not regress**:

- **Fix-Your-Timestep accumulator** (`_FIXED_DT_S = 1/240`, accumulator pattern in `_on_tick`). Drains wall-clock time in fixed-dt physics steps.
- **θ interpolation** in `_build_transform` (lerp between `_theta_prev` and `sim.theta`, with shortest-arc wrap-fix for ±π crossings).
- **AABB slack** in `_recompute_geometry` (corners at α=0 and α=2, with shortest-arc wrap-fix).
- **Per-content pixmap cache** (`_pixmap_cache` dict). This plan **changes the key** (drops `font_pointSize`) and **changes the rasterizer** (uses master font/cells), but the dict itself stays.
- **Per-glyph `painter.scale(1, line_h/ascent)` y-stretch** in the rasterizer (keeps glyph-cell aspect agreeing with block-fill rects on Windows Consolas).
- **Resize/setMask no-op guards** in `_recompute_geometry`/`_update_click_mask`.

Sources of jitter that ARE addressed by this plan:
- Cache invalidation on zoom (causes 5–15 ms stalls per zoom step).
- Sprite source invariance across the buddy's lifetime (today the source regenerates per zoom).

Sources of jitter that are NOT addressed by this plan and are likely irreducible without leaving QPainter for GPU:
- Bilinear bitmap rotation produces ~sub-pixel sampling variance frame-to-frame. Mitigated by supersampling but never zero. Game engines accept this floor.
- Sub-pixel widget position quantization (`widget.move(int(cx), int(cy))`). Could be addressed by encoding sub-pixel offset in the world transform — separate plan if it matters after this lands.
