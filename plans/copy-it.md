# Copy from chat log on Windows — F7 buddy toggle

## Goal
On Windows Terminal, Ctrl+C kills the app and Textual's mouse capture defeats terminal-native selection. Ship an F7 key that hides the buddy panel so the chat log goes full-width; then Shift+drag selects cleanly with no buddy-art interleaving, and WT's `copyOnSelect` / Ctrl+Shift+C copies as normal.

## Why this shape
- `/copy [N]` was the other candidate but the user prefers the in-terminal selection workflow once the column interleaving is gone.
- F7 mirrors the existing F2 (toggle chat log) path — minimal new surface.
- Works on every platform; on macOS/Linux it's just a bonus "read the chat log fullscreen" mode.

## Non-goals
- Not adding a `/copy` slash command (scoped out this round).
- Not changing Textual's Ctrl+C quit binding.
- Not rewriting mouse capture or the URL `@click` handlers.
- Not adding a "selection-mode cursor" or Textual ALLOW_SELECT experiments.
- Not persisting buddy-hidden state across restarts (transient UX toggle).
- Not hiding input/status — accept the short interruption while the user copies.

## Files to touch
- `tokenpal/ui/textual_overlay.py` — new `F7` binding, `action_toggle_buddy()`, `_buddy_user_hidden` state, and adjustments in `on_resize` / `_apply_chat_log_visibility` / `_apply_chat_log_width` so the full-width chat-log survives resizes until F7 toggles back.
- `CLAUDE.md` — one-line addition in the UI section listing F7 alongside F1-F4.

## Failure modes to anticipate
- **Input disappears with the buddy panel.** `#buddy-panel` contains the Input widget, so F7 makes typing impossible until toggled off. Acceptable (it's a transient select-and-copy mode) but must not deadlock — pressing F7 again must reliably restore input focus.
- **Resize while hidden.** `on_resize` currently calls `_apply_chat_log_width` which forces the stored width. Need to short-circuit when `_buddy_user_hidden` so the chat log stays full-width.
- **Chat-log auto-hide at narrow widths.** `_apply_chat_log_visibility` hides the chat log + divider below a threshold. When the buddy is hidden, that logic must flip: always show chat log, always hide divider.
- **Divider state.** Hiding `#buddy-panel` without hiding `DividerBar` leaves a stray divider at column 0. Hide both together.
- **Interaction with F2.** If the user hides the chat log (F2) and then hides the buddy (F7), the screen is blank. Not a bug — pressing either key restores something — but worth not crashing.
- **Status bar / header gone.** Users momentarily lose "mood | server | model" context. Document in CLAUDE.md so it's discoverable.

## Done criteria
- F7 hides `#buddy-panel` + `DividerBar` and makes `#chat-log` fill the width.
- F7 again restores the prior layout (buddy visible, chat-log at its prior width).
- Shift+drag in the full-width chat log selects only chat-log text (no buddy art).
- Windows Terminal `copyOnSelect` actually copies the selection; Ctrl+Shift+C also copies (no more app quit, because a selection exists for WT to intercept).
- Resize while hidden keeps the chat log full-width.
- `CLAUDE.md` UI section mentions F7.
- No regression on F1/F2/F3/F4/Ctrl+L or the existing chat-log resize/hide behavior.

## Parking lot
- `/copy [N]` slash command if the F7 workflow proves too heavy.
- Make Input widget "float" above so it stays visible even in buddy-hidden mode.
