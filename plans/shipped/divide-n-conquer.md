# divide-n-conquer

## Goal
Make the chat-log panel in the Textual overlay user-resizable via a click-and-drag divider, so research/web-search output isn't cramped in the fixed 40-cell column.

## Non-goals
- No layout overhaul of the buddy panel itself (header/speech/buddy/input/status stay as-is).
- No double-click-to-reset, no keyboard-driven resize bindings (defer to parking lot).
- No change to the auto-hide-when-narrow behavior (`_apply_chat_log_visibility`); it stays as the safety net.
- No swap to a third-party splitter dependency — build the minimum needed inline.

## Files to touch
- `tokenpal/ui/textual_overlay.py` — add a `DividerBar` widget between `#buddy-panel` and `#chat-log`; wire mouse capture / drag handlers via `MouseDown`/`MouseMove`/`MouseUp` + `capture_mouse()`/`release_mouse()`; track current chat-log width as instance state; rewrite `#chat-log.styles.width` on drag (call `refresh(layout=True)` on parent if re-layout doesn't auto-fire); load persisted width on mount; write only on `MouseUp` (drag-end), not mid-drag.
- `tokenpal/ui/textual_overlay.tcss` — add `#divider` rule (1-cell width, distinct color, `hatch` or solid bg); change `#chat-log` width from fixed `40` to a runtime-managed value (initial default still 40).
- `tokenpal/config/schema.py` — add `chat_log_width: int = 40` to `UIConfig` dataclass (line ~128-133). Loader's `_SECTION_MAP` already handles `UIConfig` generically — no per-field wiring needed.
- `tokenpal/config/ui_writer.py` — new, ~15 lines, mirrors `tokenpal/config/senses_writer.py`. Exposes `set_chat_log_width(width: int)` that calls the shared `update_config()` helper from `toml_writer.py`.
- `config.default.toml` — add `chat_log_width = 40` under the existing `[ui]` section.
- `tests/ui/` — add a unit test that constructs the divider, simulates a `MouseMove` delta, and asserts the chat-log width updates within min/max bounds. Add a second test that round-trips the width through `set_chat_log_width()` + reload, including clamp-on-load for stale oversized values. (TODO: confirm test directory exists / pattern other UI tests follow.)

## Failure modes to anticipate
- Textual has no built-in `Splitter` widget on the version we pin — need to verify which mouse events are available (`MouseDown` / `MouseMove` / `MouseUp` vs `Click`) and whether `capture_mouse()` is required to get moves outside the widget bounds.
- `#chat-log` width is currently `40` (cells). Setting `.styles.width = N` mid-run on a Textual widget may not invalidate layout — might need `refresh(layout=True)` or to set on the parent.
- Min/max bounds: if user drags past the buddy panel's `min-width: 30`, the buddy frame will get clipped (already has `overflow-x: hidden`) but speech bubble could go ugly. Need a hard floor (~30 cells for buddy, ~25 for chat per existing `min-width`). Enforce in the drag handler, not just CSS.
- Auto-hide threshold (`buddy.max_frame_width() + _BUDDY_PANEL_PADDING + _CHAT_LOG_MIN_SPACE`) currently uses `_CHAT_LOG_MIN_SPACE = 30`. If user shrinks chat-log below that, the auto-hide check on next resize will toggle it off, losing their width. Either (a) raise floor above 30, or (b) make the user-resize set a "user has touched this, respect it" flag.
- Dragging while a speech bubble is animating (typing effect on `set_interval(0.03)`) — make sure layout thrash doesn't tank the typing animation.
- Terminal mouse reporting: SSH'd terminals or some Windows terminals may not report `MouseMove` reliably. Need a graceful no-op (divider just sits there) rather than a crash.
- Persisted width from a previous session may be stale-invalid for the current terminal size (e.g. saved 120 on a 30-cell-wide terminal). Clamp to bounds on load, never crash.
- Concurrent writes: if two TokenPal instances run (rare but possible — laptop + desktop using shared `~/.tokenpal/`), last-writer-wins is fine but the writer must not corrupt the file mid-flush.
- Persistence write must be cheap and not fire on every `MouseMove` tick — write only on `MouseUp` (drag-end), not during drag.
- The chat log uses `VerticalScroll` with `scroll_end(animate=False)` after each append — re-running layout shouldn't break the scroll-to-bottom behavior, but verify.
- F2 toggle path (`action_toggle_chat_log`) and the auto-hide path (`_apply_chat_log_visibility`) both flip `chat_log.display`. Resize must coexist with both: re-showing should restore the user's last width, not the original 40.

## Done criteria
- A 1-cell vertical divider renders between buddy panel and chat log, visually distinct.
- Click-drag on the divider resizes the chat log smoothly; release commits the width.
- Bounds enforced: chat log can't shrink below 25 cells or grow such that buddy panel falls below 30 cells.
- F2 toggle still hides/shows chat log; re-show preserves the user's last drag width within the same session.
- Auto-hide on narrow terminals still triggers; growing the terminal back restores the user's last width (not the 40 default).
- Speech-bubble typing animation isn't visibly stalled by a drag in progress.
- Chosen width persists across `tokenpal` restarts on the same machine. Stale/oversized persisted values are clamped silently, never crash startup.
- One unit test covering the drag-math + bounds (no full Textual app pilot needed); a second test covering persistence round-trip + clamp-on-load.
- `ruff check tokenpal/ui/` and `mypy tokenpal/ui/textual_overlay.py` clean.

## Parking lot
(empty)
