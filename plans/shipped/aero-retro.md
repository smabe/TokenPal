# aero-retro — split the buddy's chat surface

## Goal
Break the monolithic ChatWindow into two surfaces: (1) an input + status strip anchored under the buddy that follows him as he swings, and (2) a standalone transparent frameless chat-history window with its own hide control and a scrollable message list. Give all text on the transparent surfaces a drop shadow so it stays legible against any wallpaper, and give the input a subtle "liquid-glass"-style bordered pill.

## Non-goals
- Changing the brain / orchestrator / backend in any way.
- Redesigning the status line's data sources — just its field order.
- Porting these changes to the Textual overlay; this is Qt-only polish.
- Adding window-snapping, docking behavior, or multi-history windows.
- Persisting the history window's position/visibility across restarts (phase-2 material).
- Rebuilding the chat input UX — reuse the existing `QLineEdit` from `ChatWindow`.
- Touching the speech bubble / physics integrator / tray / options dialog.

## Files to touch
- `tokenpal/ui/qt/chat_window.py` — split into two widgets: a `ChatDock` (bottom strip: input + status, frameless + transparent + anchored to buddy) and a `ChatHistoryWindow` (scrollable history list, frameless + transparent, hide button bottom-left). Preserve the existing persist/hydrate/link-click logic in the history widget.
- `tokenpal/ui/qt/overlay.py` — construct both widgets instead of one `ChatWindow`, add a `_reposition_dock()` mirror of `_reposition_bubble()` wired to `position_changed`, route `toggle_chat_log` to show/hide the history window, thread `set_status` + `append_line` + `load_history` / `clear_log` to whichever widget owns them. Reorder the status string composition (weather / voice+mood / server / model).
- `tokenpal/ui/qt/tray.py` — "Show chat" menu item now toggles the history window only (the dock is always visible with the buddy).
- `tokenpal/ui/qt/speech_bubble.py` — add a `QGraphicsDropShadowEffect` or equivalent shadow helper shared with the new widgets (or factor the shadow into a small helper module `qt/_text_fx.py` if cleaner). The same helper provides the "liquid-glass" input treatment: a stylesheet-driven rounded-rect with `rgba(255,255,255,0.12)` fill, `rgba(255,255,255,0.28)` 1 px border, inner `rgba(0,0,0,0.35)` text-contrast tint, and a soft drop shadow. No real backdrop-blur — Qt doesn't expose the macOS NSVisualEffectView without native embedding, so we fake it via alpha + border. Document in the helper's docstring so a later phase can swap in a native `NSVisualEffectView` subview on macOS if the fake falls short.
- `tests/test_qt_overlay.py` — assert `_reposition_dock` fires with `position_changed`, the history window is hidden by default, `toggle_chat_log` flips its visibility, and status composition emits `weather | voice+mood | server | model` order.
- `tests/test_qt_bubble_follow.py` — tangential: confirm the dock follows the buddy via the same signal. Or add a new `tests/test_qt_dock_follow.py` if the file grows too big.
- `docs/qt-frontend.md` — update the "Widgets" section to describe the new two-surface split.
- `CLAUDE.md` — update the Qt architecture paragraph that currently says "ChatWindow (QTextBrowser + input line)".

## Failure modes to anticipate
- **Input focus stealing**: a frameless transparent dock that always sits under the buddy will grab focus when the user clicks it. Keep `Qt.Tool` + `WA_ShowWithoutActivating` where appropriate, and only `QLineEdit` should accept focus — not the widget surface.
- **Dock follow + dangle physics**: `BuddyWindow.position_changed` fires every physics tick (60 Hz). Two followers (bubble + dock) both repositioning on every fire will spike CPU if either pulls a non-trivial layout. Keep `_reposition_dock` strictly `move()`-based, no `setGeometry` triggering relayout.
- **Multi-monitor / screen-edge clamps**: the dock must stay within the buddy's current `screen()`. Mirror the same clamp pattern already in `_reposition_bubble`.
- **Drop shadow cost**: `QGraphicsDropShadowEffect` on a frequently repainting widget (typing animation, live status updates) can hammer the compositor. Apply shadow once per widget, not per draw. If profiling shows regression, fall back to painting a manual shadow pass in `paintEvent`.
- **Chat-log hydration/persistence path**: `app.py` wires `set_chat_persist_callback`, `load_chat_history`, `clear_log`. All must continue to target the history window, not the dock. Any path still pointing at the old monolithic `ChatWindow` will break silently.
- **Textual overlay + base adapter contract**: `AbstractOverlay` methods (`toggle_chat_log`, `set_status`, `log_buddy_message`, `log_user_message`, `clear_log`, `load_chat_history`) must keep working on both overlays. Textual does nothing new; Qt routes to the new widgets. `tests/test_ui_adapter_contract.py` must still pass.
- **Transparent backgrounds + dark text**: pure transparency makes any light text unreadable on bright wallpapers and any dark text unreadable on dark wallpapers. Drop shadow has to be dense enough to halo the glyph on both ends — tune the blur + offset + color alpha on real wallpapers.
- **Dock z-order below buddy**: the dock must sit UNDER the buddy's bottom edge (touching / slightly overlapping) without covering the buddy's feet. Use `geom.bottom()` + small offset, same family as the bubble's `geom.top() - bubble_h - HOVER_OFFSET_Y`.
- **Hide button in history window**: button must be click-through-safe — clicking hide shouldn't also fire clicks on underlying chat lines. Standard `QPushButton` handles this, but make sure it's on top of the layout, not behind.
- **Status-order regression**: reordering the fields is a string-level change. Any callers parsing the old order break. Confirm `update_status` consumers don't split by `|` — grep before committing.

## Done criteria
- Launching `./run.sh` shows a buddy with a transparent, frameless input strip directly under his feet that follows him as he swings.
- Typing into that strip still submits text to the brain / slash dispatcher unchanged.
- The status bar below the input shows `weather | voice+mood | server | model` in that exact order.
- Chat history window starts hidden. Toggling via tray menu or F2 shows a separate frameless transparent window with the existing chat log scrollable inside it.
- History window has a visible "Hide" button at bottom-left, below the chat lines, that hides the window when clicked.
- The chat-history list scrolls when lines exceed the visible height, with scrollbar styled to match the transparent/glass aesthetic (no chrome-heavy default Qt bar).
- The input strip reads as a subtle translucent "glass" pill: faint 1 px border, low-alpha fill, drop shadow; placeholder and typed text both legible on bright and dark wallpapers.
- All text on the transparent surfaces (dock status, history messages, hide button) has a drop shadow that reads clearly on a bright and a dark wallpaper.
- `pytest` green, `ruff check` clean on the touched files, existing adapter-contract test passes.
- CLAUDE.md "Qt architecture" paragraph reflects the two-surface split.

## Parking lot
(empty at start)
