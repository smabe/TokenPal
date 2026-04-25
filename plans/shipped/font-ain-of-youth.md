# font-ain-of-youth

## Goal
Let users control chat-window AND speech-bubble typography: pick any OS-installed font family, set a size, and toggle bold / italic / underline for each independently. Chat window additionally supports live keyboard resize (macOS `Cmd +/-/0`, Windows/Linux `Ctrl +/-/0`). Persist choices in config so they survive restart.

## Non-goals
- Rich-text editing inside the chat input (just display styling)
- Custom theming / color picker (aero-retro skin owns color; this plan only touches typography)
- Font settings for tray menu or options dialog itself — chat window + speech bubble only
- Font import / loading custom TTFs — only fonts the OS already exposes via `QFontDatabase`
- Textual-overlay parity (Qt-only; Textual doesn't have OS font access the same way)
- Ctrl/Cmd + scroll-wheel zoom (keyboard shortcuts only; park if user asks)
- Keyboard-shortcut resize for the speech bubble (bubble is modal-ish and doesn't own focus; size via dialog only)

## Files to touch
- `tokenpal/config/schema.py` — add a reusable `FontConfig` dataclass (family, size_pt, bold, italic, underline); add two instances to `UIConfig`: `chat_font` and `bubble_font`
- `config.default.toml` — ship sensible defaults for both sections (system font; chat 13pt, bubble probably smaller per current aero-retro tuning — confirm in research pass)
- `tokenpal/ui/qt/chat_window.py` — apply `chat_font` to history + input widgets; add `QShortcut`s for zoom-in / zoom-out / reset bound to `QKeySequence.StandardKey.ZoomIn/ZoomOut` (maps to Cmd on mac, Ctrl elsewhere) + `Cmd/Ctrl+0`; write size back to config on change
- `tokenpal/ui/qt/speech_bubble.py` — apply `bubble_font` to the bubble text widget; re-render on config change. Coordinate with existing per-glyph text-shadow logic (commit `58ad816`) so shadows still look right at new size/weight
- `tokenpal/ui/qt/options_dialog.py` — two groups: "Chat font" and "Speech bubble font", each with `QFontComboBox` + size `QSpinBox` + three `QCheckBox` (bold/italic/underline) + live-preview label; save on apply
- `tokenpal/config/chatlog_writer.py` — TODO: confirm in research pass that plain-text chat log writer doesn't care about font (expected: no change)

## Failure modes to anticipate
- **Config migration**: existing users' `~/.tokenpal/config.toml` won't have `[ui.chat_font]`; loader must tolerate missing section and fall back to defaults (the `_SECTION_MAP` loader bug in issue #16 is a known footgun here)
- **QFontComboBox slowness**: enumerating every installed font on Windows can stall the dialog open; may need `QFontComboBox.setWritingSystem` or lazy population
- **Keyboard shortcut conflicts**: `Cmd+-` may collide with existing shortcuts in the chat window (e.g. menu bar, copy/paste). Audit before binding
- **Zoom bounds**: no min/max → user can press `Cmd+-` into oblivion (size 0 or negative) or `Cmd++` past usable. Clamp to a sane range (e.g. 8–48pt)
- **Font fallback**: user picks a font then uninstalls it; Qt silently substitutes. Validate family still exists on load, fall back to system default with a log line
- **Input widget vs history widget divergence**: the chat uses separate widgets for history display and input line; both must pick up the font change atomically, not one-at-a-time
- **macOS `Cmd+=` vs `Cmd++`**: US keyboards need `Shift` for `+`; use `StandardKey.ZoomIn` which handles this. Also bind bare `Cmd+=` as an alias (matches browser convention)
- **Persistence race**: if user zooms 10 times fast, each change shouldn't spam config writes. Debounce or save on close
- **Aero-retro text shadow** (recent commit `58ad816`): per-glyph shadow is tuned to a specific font size; big zooms and bubble-font changes may make shadows look wrong. Shadow offset/blur must scale proportionally to size
- **Bubble layout**: speech bubble auto-sizes to content — changing font can reflow wrap width, push the bubble off-screen, or collide with the buddy sprite. Re-run layout / clamp to screen after apply
- **Live apply vs restart**: if live-applying the bubble font requires destroying and recreating the widget (not just `setFont`), an open bubble mid-utterance could flicker or drop text. `setFont` should be sufficient — confirm in research pass

## Done criteria
- `Cmd +/-` on macOS and `Ctrl +/-` on Windows/Linux increment/decrement **chat** font size, clamped to `[8, 48]` pt
- `Cmd/Ctrl + 0` resets chat font size to config default
- Options dialog has TWO sections ("Chat font" and "Speech bubble font"), each with family picker, size spinner, and three style checkboxes; changes apply live and survive restart
- Speech bubble picks up new font immediately on apply (no buddy restart required), and a subsequent utterance lays out without clipping or collision
- Zooming via keyboard persists correctly (reflected in options dialog the next time it opens, if not open during zoom)
- Defaults ship in `config.default.toml` and a fresh `~/.tokenpal/config.toml` missing either section still loads cleanly
- Manually verified on macOS; Windows/Linux verified via code inspection (StandardKey handles the cross-platform mapping) unless user wants hands-on test

## Parking lot
(empty at start — append "ooh shiny" thoughts that surface mid-work for later)
