# get-physical

## Goal
Make the dangleable Qt buddy feel less aggressive. The headline symptom:
a cursor circle at hand-drawn speeds (~2-4 rad/s) drives the body to
its angular-speed cap (~25 rad/s), so the body whips ~8 small loops per
cursor revolution — a camshaft pattern, not the yo-yo "body trails
outward, ω_body ≈ ω_cursor" pattern the user expects. Fix that, and
calm peak speeds in straight-line flicks while we're in there.

## Mental model: yo-yo, not camshaft
Desired steady state during a cursor circle: body's COM traces a single
larger circle (radius ≈ R_cursor + L) at the cursor's rate, body angle
relative to pivot rotates at exactly `ω_cursor`. Whatever the cursor
draws, the body draws the same shape, scaled out by the rod length.
The current code instead pins ω_body to `max_angular_speed` whenever
`ω_cursor > 0.5 rad/s` — see the deliberate design comment at
physics.py:205-211 ("no real root below max_angular_speed").

## Non-goals
- Replacing the rigid-pendulum model with a multi-body rope, springs in
  parallel, or a physics engine (pymunk, etc). One pivot, one rigid arm.
- Touching the deadzone "rigid translate" path. This plan is about the
  rotational pivot-grab path only — deadzone behavior stays as-is.
- Re-tuning settle thresholds or sleep behavior. If anything those stay
  put; calmer dynamics should reach them faster.
- Changing how `_re_pivot_to_neutral` swaps the pivot back to the head on
  release. The release-time animation is fine.
- Adding a config UI / slash command to expose physics dials. Tune the
  defaults in code; expose later if needed.

## Files to touch
- `tokenpal/ui/qt/physics.py` — three structural changes:
  1. **Asymmetric tracking law** replacing the saturating
     `circular_coupling` drive: torque only accelerates body ω toward
     `ω_cursor` and stops there, never brakes an existing flick. Shape:
     `torque = coupling · (ω_cursor − θ_dot)` when same-sign and
     `|θ_dot| < |ω_cursor|`; zero otherwise.
  2. **Re-gate `spin_fade` on `ω_cursor_smoothed`, not `|θ_dot|`.**
     Current code reads `spin_fade = 1 − |θ_dot|/spin_lockout_rate`,
     which would flicker as the body tracks human cursor-speed jitter
     (notchy feel). New form: `spin_fade = max(0, 1 − |ω_cursor|/spin_lockout_rate)`.
     Rationale in failure-mode #2 below.
  3. **Dial defaults**: `circular_coupling: 12 → ~130` (math: at
     ω_c=2 rad/s the asymmetric drive needs to overcome
     ~6.9 rad/s² of damping at body ω≈1.9 → coupling ≈ 131 for 95%
     lock). `spin_lockout_rate: 4 → ~2.5`. Recheck
     `max_angular_speed`, `spin_damping_floor`, `fling_scale` once
     the saturation behavior is gone — they may want softening.
- `tests/test_qt_physics.py` — bump expected settle ticks where
  needed; add the yo-yo lock test described in "Done criteria."
- `tokenpal/ui/qt/buddy_window.py` — `_FLING_SAMPLE_WINDOW_S` and
  `_inject_fling_impulse` only if the post-release fling is what reads
  as "too aggressive." Likely a small tweak (cap the impulse, lengthen
  the sample window so a brief twitch at release doesn't dominate).
- `tests/ui/qt/test_physics.py` (or wherever the pendulum tests live —
  TODO: confirm path) — bump expected settle ticks, peak speeds in any
  golden tests we have.

## Failure modes to anticipate
- **Re-introducing the "invisible pillow" PID brake.** The existing
  comment at physics.py:198-203 explains why a symmetric PID on
  (ω_cursor − ω_body) was rejected: it brakes the body's existing
  momentum at the top of an orbit (where the body may briefly spin
  opposite the cursor while passing through). Solution above is
  asymmetric — only accelerates in cursor's direction, only when
  |ω_body| < |ω_cursor|. Verify in test that a flick followed by
  light cursor-circling does NOT decelerate the flick.
- **Cursor-rate jitter feeding into 1:1 tracking.** The whole reason
  the saturation design existed was that hand-drawn circles aren't
  uniform — `ω_cursor` jitters at turning points. With strict 1:1
  tracking, the body ω will jitter too, which may visually read as
  "body stutters during smooth-looking cursor circle." The existing
  EMA on `ω_cursor` (`circular_rate_smoothing=0.08`) helps; may need
  to be heavier (~0.04) once it's the only thing standing between
  cursor jitter and body motion.
- **Notchy spin_fade if gated on body ω.** Hand-drawn circles vary in
  speed, so body ω (tracking cursor) wobbles between ~1.5 and ~3.5
  rad/s on a "constant" circle. If `spin_fade` keeps reading
  `|θ_dot|/spin_lockout_rate`, drag/yank/gravity flicker on and off
  as the user's hand naturally varies — feels notchy, not yo-yo-smooth.
  Mitigation in change #2 above: gate on `ω_cursor_smoothed` instead.
  The 0.08 EMA already absorbs hand jitter, so the gate stays stable
  across natural variation. On release `ω_cursor` collapses to 0
  immediately, restoring full gravity for clean settle.
- **Tuning by feel without a target.** Without a way to A/B compare,
  every dial moves the buddy in *some* direction and I'll convince
  myself it's better. Fix: write down 3-4 named scenarios up front
  (slow drag, fast whip, sustained twirl, single click-and-release)
  with a one-sentence "what should feel different" for each. Tune
  against those, not against vibes.
- **Lowering gravity makes settle slow.** `gravity` is the restoring
  torque; halving it makes the pendulum period √2× longer and the
  damping ratio smaller, so the buddy oscillates more, not less, on
  release. Right knob for "less twitchy" is probably `mass` or
  `pivot_vel_smoothing`, not gravity.
- **Lowering `circular_coupling` or `max_angular_speed` kills the
  twirl.** The intentional design is "circle the cursor → orbit." If
  we just clamp the cap, sustained-twirl scenarios break. Keep the
  twirl reachable; reduce the *parasitic* spin from straight-line
  flicks instead.
- **Spin-lockout cliff feels disconnected.** The current
  `spin_fade = 1 - |ω|/spin_lockout_rate` zeros out wind-drag/yank
  at `spin_lockout_rate=4 rad/s`. That's the "cursor doesn't move
  the body during orbit" feel. If we soften this (smoother fade,
  higher ceiling) we may reintroduce the parasitic 2ω pump it was
  there to suppress. Investigate before touching.
- **Fling impulse is double-counted.** `_inject_fling_impulse` adds to
  θ_dot on release, AND the pre-release ticks already integrated
  cursor-driven torque into θ_dot. If the user flicks and lets go,
  the body has been accelerating for the last 80 ms *and* gets the
  full 80 ms-window velocity converted to angular impulse at release.
  Capping `fling_scale` further (currently 0.35) is the cheap fix.
- **Arbitrary-grab-point fundamentals.** A real object grabbed off-COM
  has the grab point as the instantaneous center of rotation, with
  body inertia I_grab = I_com + m·r² (parallel-axis). We model it as
  a rigid pendulum with `length = |grab → COM|`, which is
  geometrically correct for the rotational mode but ignores the
  inertia scaling: a foot-grab and a head-grab currently feel
  *different* mostly because of `min_length=45` clamping, not because
  of correct rotational inertia. Worth a thought experiment but
  probably not worth fixing — `min_length` already does the
  perceptual job.
- **Fast cursor traversals across the body.** When the user drags
  through the body, mouse capture means we don't get a re-press, but
  if we ever changed grab-point mid-drag the `snap_pivot` calls would
  reset velocity history. Confirm we don't accidentally regrab.

## Done criteria
- **Yo-yo test**: cursor traces a slow large circle at ~2 rad/s; body
  traces a single larger circle at the same rate (1:1 lock, no
  camshafting). Verifiable both visually and via a unit test that
  drives `set_pivot` along a circular path and asserts
  `|theta_dot − ω_cursor|` stays small in steady state.
- **Small-circle test**: cursor traces a small tight circle; body
  follows at the same rate. Currently spirals to max — should not.
- **Click-and-release** (no drag motion) produces ≤ 1 visible
  oscillation cycle before settle. No "tap and watch him swing."
- **Whip-flick** still gets the buddy over the top in a single clean
  rotation when the user clearly intends it. The asymmetric tracking
  law must NOT brake an existing flick.
- **Deliberate twirl** (sustained vigorous cursor circle ≥ ~5 rad/s)
  can still hit the angular-speed cap. We're killing the
  saturation-on-any-circling behavior, not the cap itself.
- Existing pendulum tests still pass (or are updated with a comment
  explaining the tuning change).

## Plan of attack
1. Define the named scenarios with the user before turning any dial.
   Slow drag, fast whip, sustained twirl, click-and-release —
   concrete "what feels wrong now / what should feel right" for each.
2. Read the existing pendulum test file to know what golden values
   pin down current behavior. (Step 7.5 research will confirm path.)
3. Tune one dial at a time. After each, run the buddy and check the
   scenarios. Keep a running log of (dial, old, new, scenario delta)
   in the parking lot.
4. If purely numeric tuning can't satisfy click-and-release without
   killing the whip, soften `spin_fade` or split fling into
   "press-and-hold drag-derived" vs "release impulse" buckets and
   cap them separately.

## Parking lot
(empty)
