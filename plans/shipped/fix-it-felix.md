# fix-it-felix

## Goal
Fix two Windows display bugs: (1) buddy ASCII art renders with a visible dot pattern on his body. ROOT CAUSE: the buddy art uses U+2593 DARK SHADE (`▓`) characters — by Unicode definition a 75%-shaded block, which Consolas on Windows rasterizes as a sparse dot grid. macOS Menlo renders it more densely so it reads as a smooth shade. Fix: paint Unicode block-drawing chars (U+2588, U+2593, U+2580, U+2584) as `fillRect` shapes with appropriate alpha, bypassing the font for these specific characters. Phase-1 changes (`NoSubpixelAntialias` + `TextAntialiasing`) still ship — they cleaned up an actual ClearType color-fringe artifact on the non-block characters. (2) Chat-history and news-history frameless windows have no resize affordance. Add a styled `GlassSizeGrip` to the shared `TranslucentLogWindow` base.

## Non-goals
- Reworking the buddy paint pipeline or rigid-body physics.
- Touching the macOS / Linux paths unless a single fix unifies them cleanly (no parallel "Windows-only" code paths if avoidable).
- Re-evaluating the `fusion` style decision from `14eddd9` — keep fusion (research confirmed it's not the cause of the dotted glyphs).
- Adding native window chrome / dropping `FramelessWindowHint` on the log windows. Resize must work while staying frameless + translucent.
- Rewriting `_log_window.py` — extend in place.
- Hiding the new resize grip on macOS. Per scope decision: show on every platform for consistency.
- Custom edge hit-testing — `QSizeGrip` (wrapped) is sufficient and avoids `DragHandle` collisions.

## Files to touch
- `tokenpal/ui/qt/buddy_window.py` —
  (a) Set `QFont.StyleStrategy.PreferAntialias | NoSubpixelAntialias` on the buddy font and add `TextAntialiasing` render hint. Cleans up ClearType color-fringe on non-block characters.
  (b) Add `_paint_block_char(painter, ch, rect, color) -> bool` helper. Renders U+2588 as full fill, U+2593 as 75%-alpha fill, U+2580 as upper-half fill, U+2584 as lower-half fill. Falls back to `drawText` for anything else. Helper is reuse-friendly (no buddy-specific dependencies) — ready to move to `_text_fx.py` if a second consumer surfaces. (Diagnostic dead ends ruled out along the way: fillRect-only-for-U+2588 failed because U+2593 was the dotty char, not U+2588; Win32 `DwmSetWindowAttribute` failed because it's not Acrylic; `QT_OPENGL=software` failed because it's not the GPU compositor either — none of those touched the actual font glyph.)
- `tokenpal/ui/qt/_chrome.py` — add `GlassSizeGrip(QSizeGrip)` next to `DragHandle`. Override `paintEvent` to draw three soft-white dots at ~40% alpha in the bottom-right diagonal, matching the glass aesthetic.
- `tokenpal/ui/qt/_log_window.py` — mount a `GlassSizeGrip` in the bottom row alongside the Hide button (right-aligned, after the existing `addStretch(1)`). Resize behavior comes for free from `QSizeGrip`'s built-in `startSystemResize` handling.
- `tests/test_qt_overlay.py` — add: (a) assert `TranslucentLogWindow` has a `GlassSizeGrip` child and that calling `resize()` on the window with new dimensions works (programmatic, no `mouseMove` simulation per scope decision); (b) assert `BuddyWindow._font` has `NoSubpixelAntialias` set in its style strategy.

## Failure modes to anticipate
- **Cell-width drift**: if `_measure_block_paint_width`'s `QFont` doesn't match the buddy's `QFont` style strategy, the measured cell width can be off by a pixel and break the grid alignment of the ASCII art. Sync both fonts identically.
- **`GlassSizeGrip` paint vs. functionality**: overriding `paintEvent` in a `QSizeGrip` subclass must NOT swallow the base class's mouse-event behavior. Only `paintEvent` should be overridden — let the base handle press/move/release for resize.
- **Drag-handle vs grip conflict**: `DragHandle` lives at the top, grip at bottom-right — different corners, no collision. But verify `_extras_layout` and the bottom button row don't cover the grip's hit area.
- **Multi-monitor / DPI**: `QSizeGrip` uses `windowHandle().startSystemResize` internally which delegates to the compositor; this is the right path on Wayland/Win32/macOS and doesn't require manual DPI math.
- **macOS visual**: the grip will render on macOS where users had nothing before. Per scope decision, this is acceptable (frameless window is already non-native chrome).
- **`pytest-qt` mouseMove flakiness**: `QSizeGrip` routes drags to the OS via `startSystemResize` — `qtbot.mouseMove` won't echo back synchronously on Windows CI. Test asserts grip presence + that the window honors a programmatic `resize()` call. No drag simulation.
- **First real Windows Qt session**: research confirmed `14eddd9` was likely the first Windows session where PySide6 actually rendered (it added the `desktop` extra to `run.ps1`). The "anymore" framing was misleading — neither bug is a regression, but the fix is the same.

## Done criteria
- On Windows, buddy ASCII art renders as solid blocks (no dotted/textured fill) — confirmed visually by user.
- On Windows, chat history and news history windows can be resized via the bottom-right grip — confirmed by user.
- macOS buddy and log-window appearance unchanged in any way that matters (grip now visible on log windows is expected; buddy glyphs unchanged because `NoSubpixelAntialias` is a no-op on macOS CoreText) — confirmed by user OR by reasoned argument from the diff.
- Tests added: (a) `GlassSizeGrip` is present in the `TranslucentLogWindow` widget tree and programmatic `resize()` works; (b) `BuddyWindow._font` carries `NoSubpixelAntialias`.
- `pytest`, `ruff check tokenpal/`, `mypy tokenpal/ --ignore-missing-imports` all green.

## Done criteria
- On Windows, buddy ASCII art renders as solid blocks (no dotted/textured fill) — confirmed visually by user.
- On Windows, chat history and news history windows can be resized (by drag from corner or edge) — confirmed by user.
- macOS buddy and log-window appearance unchanged — confirmed by user OR by reasoned argument from the diff.
- Tests added covering: log window has a resize affordance present in its widget tree; buddy paint sets text-antialias hint (or whichever specific fix lands).
- `pytest`, `ruff check tokenpal/`, `mypy tokenpal/ --ignore-missing-imports` all green.

## Parking lot
- Sweep other translucent widgets (`speech_bubble.py`, `weather.py`, `_log_window.py`'s `QTextBrowser`) for the same QTBUG-43774 dotted-glyph artifact on Windows. If a second site hits it, factor `disable_subpixel_aa(font)` into `_text_fx.py` and apply at `qt_font_from_config` so all glass surfaces get it for free. Premature today — only the buddy is reproducing the bug.
