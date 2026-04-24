# swing-it — pendulum drag physics

## Goal
Give the buddy proper pendulum physics when the user drags him around: when the
cursor moves right he tilts LEFT (body trails behind the head), when the cursor
moves left he tilts RIGHT, and on release his body swings back through rest
with a few seconds of residual angular momentum before damping out.

## Model
Rigid pendulum pivoted at **wherever the cursor grabbed the buddy**. On
`mousePressEvent` we capture the cursor's position in widget-local coords as
`pivot_local`; the pivot follows the cursor rigidly for the rest of the drag,
and the rest of the buddy rotates around it. Grab by the head → dangles like
a hanged figure. Grab by the foot → dangles upside-down. Pendulum length
`L` = distance from `pivot_local` to the buddy's center of mass (widget
center, computed once at press time).

State is angle `theta` (rad, clockwise-from-down, positive = body swung
right) and `theta_dot`. No translational spring.

ODE in screen coords (y down, `theta` = CW angle of body from straight-down):

    theta'' = -(g * sin(theta) + a_x * cos(theta) - a_y * sin(theta)) / L
              - c * theta'

where `(a_x, a_y)` is pivot acceleration (finite-diff of pivot position over
recent ticks), `g` is gravity, `L` is pendulum length (≈ buddy height), `c` is
angular damping. Steady state with `a_x > 0` gives `theta ≈ -a_x / g` — body
hangs to the left when the pivot accelerates right. Release stops the pivot,
residual `theta_dot` carries the swing through rest, damping settles it.

Auto-sleep when `|theta|` and `|theta_dot|` stay under thresholds for N ticks
(mirrors existing `DangleSimulator` settle logic).

## Files to touch
- `tokenpal/ui/qt/physics.py` — add `PendulumSimulator` class alongside
  `DangleSimulator`. Same public shape (`set_pivot`, `tick(dt) -> theta`,
  `apply_angular_impulse`, `sleeping`, `run_until_settled` helper generalized).
  Finite-diff pivot acceleration via a small ring buffer of recent pivot
  samples.
- `tokenpal/ui/qt/buddy_window.py`:
  - Drop the 2D translational spring. Window position moves so that
    `pivot_local` (captured at press time) lands under the cursor.
  - Swap `DangleSimulator` for `PendulumSimulator`.
  - In `paintEvent`: `translate(pivot_local)` → `rotate(degrees(theta))` →
    `translate(-pivot_local)` → draw art as before. Rotation is around the
    grab point.
  - Resize widget to fit art rotated a full 360° around the worst-case
    pivot (a grab in a corner). Simplest: pad by
    `max(pivot_local_x, W - pivot_local_x, pivot_local_y, H - pivot_local_y)`
    on each side — guarantees no clipping at any angle. Re-pad on each
    press when `pivot_local` changes; repaint handles the rest.
  - Expose `head_world_position()` that returns the head-anchor point
    (top-center of the *original* art) after rotation, in global screen
    coords. Emit `position_changed` on every tick so the speech bubble
    can re-query and follow the rotating head.
  - Fling on release → angular impulse (tangential component of cursor
    velocity at the pivot, divided by `L`) instead of translational
    impulse.
- `tokenpal/ui/qt/speech_bubble.py` (and/or its positioning caller in
  `chat_window.py` / `app.py` — trace on first read):
  - Switch from "follow top-center of buddy widget rect" to "follow
    `BuddyWindow.head_world_position()`". Keep bubble upright (don't
    rotate the bubble itself) — only the anchor point moves. Upside-down
    grabs are the joke; a right-side-up bubble floating off his feet is
    the punchline.
- `tests/ui/qt/test_physics.py` (if it exists — else create):
  - Unit tests for `PendulumSimulator`: rest state, steady-state tilt under
    sustained pivot acceleration, free-swing decay, settle sleep.

## Non-goals
- Do NOT keep the existing 2D translational spring. It's being replaced; no
  composite "translates AND rotates" model.
- Do NOT rotate the speech bubble itself. Bubble text stays upright; only
  its tail/anchor tracks the rotating head.
- Do NOT change edge-dock snap behavior.
- Do NOT tune physics numbers per-machine / per-config. One set of constants,
  tuned once.
- Do NOT touch Textual / tkinter overlays — Qt only.

## Failure modes to anticipate
- **Rotation clips window edges**: Qt widget is sized to upright art; rotated
  art pokes outside. Need extra horizontal + vertical padding sized for the
  max expected `|theta|`.
- **Widget position vs. pivot**: `_move_to_body_position` currently offsets
  by `width // 2`. New model positions the widget so that the captured
  `pivot_local` lands under the cursor — different offset, and window size
  itself changes per-grab because of rotation padding.
- **Finite-diff acceleration noise**: cursor samples can be jittery at 60 Hz.
  Need to smooth pivot-acceleration (EMA or windowed average) or the buddy
  will flutter instead of swing.
- **Small-angle vs. large-angle**: `sin(theta)` is fine for all angles but
  steady-state formula `theta ≈ -a_x/g` breaks down past ~30°. Clamp
  `|theta|` or just accept the nonlinearity — it's a pendulum, that's the
  point.
- **Release while tilted**: cursor stops mid-swing, `theta` is non-zero,
  pivot acceleration drops to 0, pendulum must swing naturally. Make sure
  the angular impulse from fling samples adds to `theta_dot`, doesn't
  overwrite it.
- **Edge dock snap mid-swing**: if dock fires while `theta_dot` is big, the
  snap shouldn't kill the angular velocity. Keep snap as a pure pivot
  translation; leave the angular state alone.
- **macOS frameless focus quirk**: per `project_qt_frameless_focus` memory,
  frameless NSWindows sometimes need `activateWindow()` — shouldn't apply
  here (we're not taking focus) but flag if drag input goes weird.
- **Speech bubble lag**: bubble must re-query `head_world_position()` every
  tick — not just on window move — or it'll desync during post-release
  swings (window stationary, art rotating inside). Wire `position_changed`
  to fire on every physics tick, not just on `move()`.
- **Bubble path-finding**: bubble placement logic probably picks "above
  head" vs. "below head" based on screen edges. When the buddy's grabbed
  by the foot and his head is near the screen bottom, the bubble-above
  logic may flip. Accept whatever happens for v1; park if it looks bad.
- **Grab point at the center of mass**: if pivot_local equals the COM,
  `L = 0` and the angular ODE divides by zero. Clamp `L` to a minimum
  (e.g. 8 px); a grab exactly at the COM just means a near-zero lever
  arm and the buddy barely swings, which is physically correct.
- **Painter rotation + `WA_TranslucentBackground`**: rotating the painter
  should compose fine with translucent clear, but confirm the rotated art
  doesn't smear/ghost across ticks (need `update()` per tick, already
  wired).

## Done criteria
- Slow drag right, grabbed by the head: buddy visibly tilts left while
  being dragged, snaps back to upright on release with a short damped
  swing.
- Fast fling right, grabbed by the head: buddy tilts hard left, releases
  with visible overshoot and 2-4 visible swings before settling.
- Grabbed by a foot and moved: buddy dangles upside-down from the foot,
  head swings around below the grab. Speech bubble (if visible) follows
  the head, stays right-side-up.
- Pendulum tests in `tests/ui/qt/test_physics.py` pass: rest, steady-state
  tilt direction, free-swing decay, sleep.
- `pytest` green, `ruff check tokenpal/` clean, `mypy tokenpal/` clean.
- Speech bubble still reads as attached to the head under drag + swing
  (visual check, not a test).
- No new warnings in `tokenpal --verbose` launch.

## Parking lot
(empty at start)
