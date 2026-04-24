# Chat-history background + font color pickers

## Goal
Let the user pick both the chat history window's background color AND the
log text color. Today background is hard-coded black behind the opacity
slider, and log text is hard-coded `#ffffff` in the QTextBrowser stylesheet
(chat_window.py:234). Persist both in `[chat_log]` and apply live via the
Qt options dialog.

## Non-goals
- Not changing the opacity slider mechanics — background color is orthogonal,
  stacks on top of the existing `background_opacity` alpha.
- Not theming scrollbars, drag handle, Hide button, ChatDock input, or the
  status strip (`"ready"` label at chat_window.py:147).
- Speech bubble colors REUSE the same `[chat_log] background_color` and
  `font_color` keys — no separate bubble config surface. Bubble alpha stays
  at its hard-coded 232/255 (independent of the chat_log opacity slider)
  so a fully-transparent history panel doesn't make the bubble vanish too.
- Not recoloring the per-line timestamp (currently `#bbbbbb` inline in the
  HTML at chat_window.py:327). It stays muted. Author name inherits the main
  text color automatically, so it'll track the new font color for free.
- Not wiring this into the Textual fallback overlay (Textual already skips
  the opacity slider — same treatment here).
- Not adding slash commands — options dialog only, matching opacity.
- Not adding presets, palette swatches, or accent-color inference from the
  wallpaper. Two color pickers, two hex strings.
- Not auto-correcting bad contrast combos (e.g. white text on white bg). If
  the user picks an unreadable pair, that's on them — we're giving them the
  knobs they asked for.

## Files to touch
- `tokenpal/config/schema.py` — add `background_color: str = "#000000"` and
  `font_color: str = "#ffffff"` to `ChatLogConfig`.
- `tokenpal/config/chatlog_writer.py` — add a shared `normalize_hex_color(s)`
  helper + `set_background_color(s)` and `set_font_color(s)` writers.
- `tokenpal/ui/qt/chat_window.py` —
  - Store `_background_color: QColor`; update `set_background_opacity` to
    compose alpha onto the stored color; add `set_background_color(hex)`
    that rebuilds the brush with current alpha.
  - Add `set_font_color(hex)` that re-emits the `QTextBrowser` stylesheet
    with the new `color:` value (preserving `background: transparent`,
    padding, and the glass scrollbar stylesheet append).
- `tokenpal/ui/options_modal.py` — extend `OptionsModalState` with
  `chat_history_background_color: str = "#000000"` and
  `chat_history_font_color: str = "#ffffff"`; extend `OptionsModalResult`
  with `set_chat_history_background_color: str | None = None` and
  `set_chat_history_font_color: str | None = None`.
- `tokenpal/ui/qt/options_dialog.py` — add two rows under the opacity slider:
  "Background color" and "Log text color", each a swatch button that opens
  `QColorDialog`. Each live-previews via a new callback, remembers initial
  for Cancel revert. Factor a `_revert_preview()` helper so Cancel restores
  opacity + bg color + font color in one place.
- `tokenpal/app.py` — feed both colors in from `cl.background_color` /
  `cl.font_color`, handle both result fields symmetrically with opacity
  (persist via writer, mutate `cl.*`, call the new overlay setters), pipe
  preview callbacks through.
- `tokenpal/ui/qt/overlay.py` — add `set_chat_history_background_color(hex)`
  and `set_chat_history_font_color(hex)` forwarders that ALSO update the
  speech bubble (one preview callback, two sinks).
- `tokenpal/ui/qt/speech_bubble.py` — swap module-level `_BG` / `_FG`
  constants for instance attrs; add `set_bubble_colors(bg_hex, fg_hex)`
  that keeps the existing 232/255 alpha on the bg QColor; repaint.
- `tests/` — unit tests for `normalize_hex_color` (accept `#rrggbb`,
  `#RRGGBB`, reject garbage → fall back to default) and smoke tests that
  `ChatHistoryWindow.set_background_color` + `set_background_opacity`
  compose correctly on `_background_brush`, and `set_font_color` updates
  the QTextBrowser stylesheet without stripping the scrollbar rules.

## Failure modes to anticipate
- `WA_TranslucentBackground` + stylesheet combo has bitten this file before
  (see CLAUDE.md "Qt painting/translucency convention"). Background must
  stay on the `paintEvent` path, not a stylesheet gradient. Font color is
  fine via stylesheet because the QTextBrowser itself isn't translucent at
  the Qt level — only its viewport is — and text color already flows through
  the stylesheet today.
- Existing configs have no `background_color` / `font_color` keys — the
  loader must tolerate absence (dataclass defaults handle this, but verify
  the TOML reader doesn't barf on missing keys the way `_SECTION_MAP` has
  before — see issue #16).
- Invalid hex in a hand-edited config.toml must not crash startup. Normalize
  at load time via `normalize_hex_color`, not just in the writer — apply to
  both fields.
- `QColorDialog` on macOS steals activation from the buddy window; opening
  it from inside the already-floating Options dialog should be fine because
  that dialog is a real `QDialog`, but verify the picker closes and returns
  focus cleanly (WA_ShowWithoutActivating interactions bit us before).
- Cancel path must revert live previews back to initial opacity + bg color +
  font color. Factor a `_revert_preview()` helper so we don't end up with
  three parallel revert branches that drift out of sync.
- Persist timing: user may move the slider, change bg color, change font
  color, then Save. Three sequential writer calls match local precedent
  (fonts already do this), but double-check we don't emit three buddy log
  lines — collapse to one summary line or keep silent when values unchanged.
- Font color live-update must preserve the scrollbar stylesheet append and
  the `background: transparent` rule. If we re-emit the stylesheet by
  string-formatting, a typo silently blanks the scrollbars. Add a smoke
  test that asserts the scrollbar stylesheet substring is still present
  after `set_font_color` runs.
- Author name HTML uses no explicit color (inherits), so it'll track the
  new font color automatically. Timestamp HTML hard-codes `#bbbbbb` — stays
  fixed per non-goals. Confirm this visually; if the timestamp looks awful
  against certain font colors, revisit in a follow-up, not here.

## Done criteria
- `~/.tokenpal/config.toml` round-trips `[chat_log] background_color = "#..."`
  and `[chat_log] font_color = "#..."`.
- Picking either color in /options updates the history window instantly
  (live preview). Cancel reverts all three (opacity + bg + font). Save
  persists.
- Defaults (no keys set) render identical pixels to today: pure-black panel
  at matching opacity, white log text — verified by eye on macOS.
- Unit tests cover `normalize_hex_color` accept/reject, the compose-with-
  opacity path on `ChatHistoryWindow`, and that `set_font_color` preserves
  the scrollbar stylesheet.
- `mypy tokenpal/ --ignore-missing-imports` and `ruff check tokenpal/` clean.
- `pytest` green.

## Parking lot
(empty at start — append "ooh shiny" thoughts that surface mid-work for later)
