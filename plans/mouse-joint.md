# mouse-joint

## Goal
Replace the Qt buddy's rigid-pendulum + asymmetric-tracking +
wind-drag + spin-fade + fling-impulse stack with a single
mouse-joint-style soft constraint, ported from Erin Catto's
*Soft Constraints* (GDC 2011). Buddy becomes a 2D rigid body with
free `(x, y, θ)` state; the cursor is the constraint anchor while
grabbed; release destroys the constraint and the body coasts.

## Non-goals
- Multi-segment ragdoll. Buddy stays one rigid body.
- Adding a physics engine dependency (pymunk, Box2D bindings). We
  port ~50 lines of constraint math directly into
  `tokenpal/ui/qt/physics.py`. If the port is uglier than the
  dependency, surface as a scope question — don't silently grow it.
- Reusing any of the asymmetric-tracking / spin-fade / wind-drag /
  fling-impulse code paths. Those go away in their entirety. Keep
  `DangleSimulator` (separate, used by other entities) untouched.
- Touching slash commands, brain, senses, or anything outside the
  Qt buddy window's grab/drag/release loop.
- Re-exposing tuning via slash commands or a settings UI. Defaults
  in code; expose later if needed.
- Backwards compatibility with the old `PendulumConfig` dial names.
  The new config replaces the old one — no shim, no aliases.

## Conceptual model
Body state: `(x, y, θ)`, `(vx, vy, ω)`, scalar mass `m`, scalar
moment of inertia `I` (cylinder approximation: `I = m·R²/2` where
`R` is body radius from COM, computed once from art bounds).

**Per tick (no grab):**
1. Apply external forces. Gravity pulls COM toward a screen-anchor
   target via a stiff spring-damper (configurable Hz + ζ) so the
   buddy returns home after release. Without this the body would
   either fall off the screen or sit motionless wherever it lands —
   neither is what we want for a desktop buddy.
2. Semi-implicit Euler integrate: `v += a·dt; x += v·dt`.

**Per tick (grab active):**
1. Compute Jacobian for the constraint at the grab anchor:
   `r = R(θ) · anchor_local` (anchor in world, relative to COM).
   `J = [I₂ | skew(r)]` so `Cdot = v + ω × r − v_target`.
2. Compute effective mass `K = J · M⁻¹ · J^T` (a 2×2 matrix; the
   parallel-axis term `I_grab = I + m·|r|²` falls out of this).
3. Compute soft-constraint coefficients from frequency `f` and
   damping ratio `ζ`:
     ω_n = 2π·f
     k = m_eff·ω_n²;   c = 2·m_eff·ζ·ω_n
     γ = 1 / (dt·(c + dt·k));   β = dt·k·γ
4. Solve velocity-level constraint:
     C = world_anchor − cursor_pos
     P = −(K + γ·I)⁻¹ · (Cdot + β·C/dt + γ·P_acc)
5. Apply impulse: `v += m⁻¹·P;  ω += I⁻¹·(r × P)`.
6. Accumulate `P_acc += P` (warm-start next tick).
7. Integrate position with same semi-implicit Euler as no-grab path.

**On release:** drop the constraint state. No fling impulse, no
windowed velocity sample, no `_inject_fling_impulse`. Body coasts
with current `(v, ω)`; the home-spring keeps pulling it back.

## Files to touch
- `tokenpal/ui/qt/physics.py` — gut `PendulumSimulator` /
  `PendulumConfig` and replace with `RigidBodySimulator` /
  `RigidBodyConfig`. Keep `DangleSimulator` intact (used by other
  entities; not part of buddy-window grab loop). New API:
    - `state: (x, y, theta, vx, vy, omega)`
    - `set_anchor(world_x, world_y) / clear_anchor()`
    - `set_anchor_offset(local_x, local_y)` — grab point on body
    - `set_target(world_x, world_y)` — cursor position each tick
    - `tick(dt)`
    - `apply_impulse(px, py, at_local=None)` for any external
      impulses (likely unused at first)
- `tokenpal/ui/qt/buddy_window.py` — rewrite the grab/drag/release
  path to use the new simulator. Specifically:
    - `_begin_drag` — set anchor offset (= grab pixel relative to
      COM), set anchor target = cursor, no `snap_pivot`, no
      `reset_angle`, no `_fling_samples`.
    - drag loop — `set_target(cursor_x, cursor_y)` each mouse-move,
      let physics solve.
    - `_end_drag` — `clear_anchor()`. Delete
      `_inject_fling_impulse` and the `_fling_samples` deque.
    - `_pendulum_length` and rest-tilt clamp logic — gone. The
      body's rotation is just θ, not split into "physics theta +
      visual offset."
    - Deadzone "rigid translate" path — **delete**. Under a real
      mouse joint, a center-of-torso grab naturally translates
      with little rotation because `r ≈ 0` makes the angular
      impulse term `I⁻¹·(r × P)` vanish. Smooth gradient replaces
      the old hard threshold. Verify by feel during phase 2 smoke
      test; if center-grabs feel wrong, that's a finding worth
      surfacing, not a reason to resurrect the special case.
    - `_PIVOT_TILT_CLAMP_RAD`, `_FLING_SAMPLE_WINDOW_S`, and the
      anti-stable-equilibrium nudge (`_PIVOT_PI_NUDGE`) — all
      pendulum-era artifacts; delete.
- `tests/test_qt_physics.py` — gut and rewrite. New tests:
    - **Yo-yo lock**: cursor traces a circle; body's grab anchor
      tracks cursor within a small tolerance after settle.
    - **Off-COM rotation**: grab at body edge, target moves
      laterally; body rotates more than translates. Grab at COM,
      same input; body translates more than rotates. The ratio
      validates the parallel-axis term is being applied.
    - **Release coast**: spin up body via constraint, release;
      body retains ω, decays only via home-spring damping. No
      sudden velocity jump on release (this is the
      double-counting regression test).
    - **Click-and-release**: zero motion in, ≤ 1 oscillation
      cycle out via home-spring critical damping.
    - **Home-spring settle**: body displaced from anchor with no
      grab returns to anchor in bounded time.
    - **Soft-constraint stability**: at f = 8 Hz, ζ = 1.0, dt =
      1/60, no NaN, no exponential blowup over 600 ticks.
- `tokenpal/ui/qt/_text_fx.py` / overlay rendering — read-only.
  The renderer just needs `(x, y, θ)` from the simulator; old
  pendulum geometry helpers (`_recompute_geometry`,
  `_angle_of_com_offset`) are gone, but nothing outside
  `buddy_window.py` should care.

## Failure modes to anticipate
- **Home-spring is a new failure surface.** Without a pendulum
  pivot pinning the head to the screen anchor, the buddy needs
  *some* restoring force or he wanders / falls. The home spring
  is the cheapest answer but introduces a second tunable
  (frequency Hz, damping ratio). Critically damped (ζ = 1) at a
  modest frequency (~2 Hz) is a sensible default; verify it
  doesn't fight the grab when both are active.
- **Two simultaneous constraints (grab + home).** While grabbed,
  both forces apply. They'll fight near the home position and
  cooperate far away. If the grab-spring is much stiffer than the
  home-spring (which it should be — grab is "fast follow," home
  is "slow return"), this is fine, but verify.
- **Inertia model is approximate.** A scalar `I = m·R²/2` treats
  the body as a uniform disk. ASCII art is not a uniform disk —
  the head is denser than the legs. This is fine for feel; flag
  if rotation feels weirdly heavy or light.
- **Drift / jitter from γ regularization.** Soft constraints with
  high frequency + low damping can ring; high damping + low
  frequency can lag. Default to (8 Hz, ζ = 1.0) and adjust by
  feel only after the rewrite is structurally correct.
- **Cursor jitter still feeds in.** At 1:1 lock the body will
  jitter with hand-drawn circle imperfection. The home-spring
  damping doesn't help during grab; the constraint frequency does
  (lower f = softer follow). May need an EMA on the cursor target
  the way the old code did. Resist adding it preemptively;
  measure first.
- **Frame-rate dependence.** Soft-constraint γ/β derivation is
  explicitly dt-dependent; that's correct, but only if dt is
  consistent. Buddy currently runs at a 60 Hz timer. Confirm
  the timer interval before tuning, and consider clamping the
  per-tick dt to a max if Qt drops frames.
- **Grab start glitch.** First tick after `_begin_drag` has no
  warm-start `P_acc`. Initialize to zero; expect one tick of
  larger correction. If visible, prime `P_acc` from the
  difference between cursor and current anchor.
- **Releasing while flicking sends the buddy off-screen.** Under
  a stiff home-spring this self-corrects, but a hard whip might
  fly past the visible area before the spring catches. Decide
  whether to clamp body position to the desktop bounds (cheap)
  or accept the brief off-screen excursion (probably fine).
- **Replacing `PendulumSimulator` breaks anything else that imports
  it.** Grep for callers before deleting. `DangleSimulator` is the
  shared utility used by other animations — leave that alone.
- **The whole rewrite might feel worse than what we have.** User
  pre-authorized this risk: "if it's not right we throw it away."
  The lesson-summary at ship time covers it either way.

## Done criteria
- `RigidBodySimulator` lives in `tokenpal/ui/qt/physics.py` with
  the API listed in "Files to touch."
- `buddy_window.py` uses the new simulator end-to-end. Old
  pendulum imports / helpers / constants removed.
- `_inject_fling_impulse`, `_fling_samples`, `_PIVOT_TILT_CLAMP_RAD`,
  `_PIVOT_PI_NUDGE`, `_FLING_SAMPLE_WINDOW_S`, `_angle_of_com_offset`,
  `_recompute_geometry` (anything pendulum-specific) — all deleted.
- New test file passes and covers the six tests listed above.
- Existing buddy-window tests that don't touch physics
  (e.g. dock-follow, edge-dock, shell, slash-dispatch) still pass
  unchanged.
- `mypy tokenpal/ --ignore-missing-imports` and `ruff check
  tokenpal/` clean.
- Manual smoke test: launch buddy, grab and drag through each of
  the five named scenarios from get-physical (slow drag, fast whip,
  sustained twirl, click-and-release, off-COM grab) and record
  one-line "feels right / feels off" verdict per scenario.
- Lesson summary written: a short retrospective comparing
  pendulum-era and mouse-joint-era feel, what the rewrite cost
  (LOC delta, time spent, failures encountered), and whether the
  research's "structurally correct" pitch held up perceptually.
  Lives at the end of this plan file before shipping.

## Plan of attack
1. **Phase 1 — solver in isolation.** Write `RigidBodySimulator`
   with home-spring + grab-constraint solver in `physics.py`,
   plus the new test file. No buddy-window changes yet. Phase
   ends green: test file passes, mypy/ruff clean.
2. **Phase 2 — wire into buddy_window.** Replace the grab/drag/
   release loop. Delete pendulum-era code in the same commit so
   the old code isn't dragging behind. Run buddy locally; verify
   the five scenarios at least *function* (no crash, no NaN,
   buddy moves with cursor and returns home).
3. **Phase 3 — tune Hz + ζ defaults.** Single dial-tuning pass
   against the five scenarios. Keep notes per scenario. If a
   single pair of (grab f, grab ζ, home f, home ζ) can't satisfy
   all five, that's a finding — surface it before adding more
   dials.
4. **Phase 4 — write the lesson summary.** Either "rewrite paid
   off" or "rewrite was the wrong call, here's what to revert
   to," with concrete observations. Append to this plan, then
   ship to `plans/shipped/` along with `fling-me.md`.

## Parking lot
- If the buddy's COM linear motion ends up feeling too "floaty,"
  consider a small linear damping (analogous to old
  `pivot_vel_smoothing`) applied to vx, vy independent of the
  home-spring. Don't add preemptively.
- Memory `project_qt_physics_handover.md` becomes stale once the
  rewrite ships (no more wind-drag / orbit-tracking handover).
  Update or delete it during the post-ship cleanup.
- `get-physical.md` already shipped; its tuning is invalidated
  but its mental-model writeup (yo-yo vs camshaft) is still
  useful context for the lesson-summary section.
- Deadzone rigid-translate path is being deleted in phase 2 (per
  Files-to-touch). If center-grabs feel different post-rewrite,
  that's expected behavior — the smooth `r × P` gradient replaces
  the old hard threshold.
