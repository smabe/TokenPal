# see-thru

## Goal
Apply the per-glyph dense-halo drop shadow (`blur=4`, `offset=(0, 0)`,
`color=QColor(0, 0, 0, 255)`) to the docked chat input `QLineEdit`,
matching the treatment commit `58ad816` ("qt-aero-retro: proper per-
glyph text shadow") gave the chat-history viewport and the dock's
status label. The same commit missed the input pill, so the
placeholder + typed text still wear the old soft directional shadow
(`blur=10`, `offset=(0, 2)`, alpha 220) and disappear into busy
wallpaper.

## Non-goals
- Not adding any user-facing slider, opacity knob, or backdrop paint.
  No `paintEvent` on `ChatDock`. No new config keys. Current pill
  opacity (`rgba(255, 255, 255, 0.12)` from `glass_pill_stylesheet`)
  stays exactly as it is.
- Not touching the status label (already correct after `58ad816`).
- Not touching `TranslucentLogWindow`, news window, speech bubble,
  options dialog, or app-level wiring.
- Not refactoring the dock's layout, focus policy, or reparenting.

## Files to touch
- `tokenpal/ui/qt/chat_window.py` — change the
  `apply_drop_shadow(self._input, blur=10, offset=(0, 2))` call
  (line 75) to `apply_drop_shadow(self._input, blur=4, offset=(0, 0),
  color=QColor(0, 0, 0, 255))`. One line.
- Possibly a tiny test asserting the shadow params on the dock's
  input match the status label's, if the existing test layout makes
  that straightforward — otherwise visual verification only (the
  paint output isn't easily asserted headlessly).

## Failure modes to anticipate
- **QLineEdit isn't a QAbstractScrollArea.** The chat-history fix in
  `58ad816` worked because the effect went on the *viewport*, leaving
  the frame's outer composite alpha out of the blur. `QLineEdit`
  has no viewport — the drop-shadow effect runs over the WHOLE widget
  composite, including the pill background fill from
  `glass_pill_stylesheet`. Risk: the dense halo blurs the pill's
  rounded-rect silhouette, producing a visible black halo around the
  pill border in addition to the per-glyph halo we want. Visual
  verification on a busy wallpaper is required before declaring done.
  If the pill-border halo looks bad, the deeper fix (deferred unless
  we hit it) is to drop the `background` rule from
  `glass_pill_stylesheet` for this caller and paint the pill via a
  parent `paintEvent` so the QLineEdit only renders glyphs — that's
  out of scope for this plan and goes to the parking lot if it bites.
- **Embedded-dock state.** When the buddy is hidden, `ChatDock` is
  reparented INTO the chat-history window's `_dock_slot`. The new
  shadow needs to look right both floating under the buddy AND sitting
  above the history backdrop. Verify in both states.
- **Shadow parameter creep.** Don't tune `blur` / `offset` "to taste"
  beyond the values `58ad816` already validated. Match the status
  label exactly so the two rows in the dock look like siblings.

## Done criteria
- The dock's input pill placeholder + typed text are as legible as
  the status row below it against busy wallpaper (visual: open the
  buddy on the user's mosaic-tile wallpaper; placeholder reads
  cleanly).
- The pill silhouette doesn't sprout an obviously ugly halo around
  its rounded border. If it does, surface and re-plan — don't ship.
- Same legibility holds when the dock is embedded in the history
  window (buddy hidden state).
- `pytest`, `ruff check tokenpal/`, `mypy tokenpal/
  --ignore-missing-imports` all green.

## Parking lot
(empty)
