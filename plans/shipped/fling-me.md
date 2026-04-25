# fling-me

## Goal
Research how mainstream game engines and physics libraries actually
implement grab/drag/fling/release for an off-COM rigid body, then decide
whether the qt-buddy physics is genuinely under-modelled (worth a
structural rewrite) or just under-tuned (keep the rigid-pendulum and
move dials). The user's gut says we're papering over fudge factors
where Unity/Unreal/Box2D would just solve the constraint cleanly.

## Non-goals
- Touching any physics code in this plan. This is research → revised
  plan → user approval. Implementation is a follow-on plan.
- Adopting a full physics engine dependency (pymunk, Chipmunk, Box2D
  Python bindings). Even if the research recommends a constraint
  solver approach, we evaluate it as "implement the relevant 50 lines
  ourselves" first — TokenPal is a desktop buddy, not a game.
- Re-tuning dials. The previous get-physical plan already did the
  defensible numeric tuning pass. If research says "you got the model
  wrong," we replan; if it says "you got the model right, the dials
  are fine," we close this plan and move on.
- Ragdoll multi-segment work. The buddy is one rigid body; whatever
  ragdoll teaches us about constraint chains is interesting but only
  applies if we ever multi-segment the buddy (we won't, near-term).

## What "feels off" — symptom inventory
The user can't put a finger on it. Before research, capture concrete
hypotheses so the agents have something to verify or rule out:
- Off-COM grab feels rotationally lighter than it should — possibly
  because we model it as a pendulum of length r without the
  parallel-axis-theorem inertia bump (I_grab = I_com + m·r²). Game
  engines using a constraint solver get this for free.
- Release impulse may be double-counted: pre-release ticks already
  integrated torque into θ_dot, then `_inject_fling_impulse` adds the
  windowed average velocity again. Fling-from-physics is the
  textbook approach (just stop applying the constraint, body
  continues with current state) and would not need a separate
  impulse injection.
- Wind-drag vs orbit-tracking handover (per project memory:
  "symmetric handover gated by complementary fractions of ω_cursor")
  is a hand-rolled blending heuristic where a constraint-based grab
  would have a single mode: the cursor IS the constraint anchor;
  there's no second "wind drag" force.
- Yo-yo lock at 1:1 ω_cursor was hard-won and stable, but the fact
  that we needed an *asymmetric* tracking law to avoid braking
  existing flicks suggests the underlying model isn't quite the
  constraint we want.

## Research questions (for the Explore agents in step 7.5)
1. **How does Unity's `Rigidbody2D` + `TargetJoint2D` (or Unreal's
   physics-handle / `PhysicsHandleComponent`) implement grab-and-drag
   for a 2D rigid body grabbed off-COM?** Specifically: what's the
   constraint, how is it solved per-tick, what happens on release?
2. **How does Box2D / Chipmunk implement a "mouse joint"?** This is
   the canonical primitive for "drag a rigid body with the cursor."
   What are the parameters (frequency, damping ratio), what's the
   math (soft constraint via Baumgarte stabilization or position
   projection), and how does release work?
3. **Verlet vs semi-implicit Euler vs RK4** for a draggable rigid
   body — which does Box2D / Bullet / Unity Physics2D actually use,
   and why? Our integrator is plain explicit Euler in physics.py;
   is that the source of "close to natural but not quite right"?
4. **Constraint-based grab vs spring-damper grab** — Unity's
   default `TargetJoint2D` is a soft (spring) joint with frequency
   + damping. Box2D's mouse joint is also a soft constraint. Are
   they spring-dampers under the hood, or hard constraints with
   Baumgarte? What are the perceptual differences?
5. **Ragdoll fling on release**: in Unity/Unreal ragdolls, when the
   player lets go of a grabbed limb, what is the engine *actually*
   doing? Is there a separate impulse injection (like our
   `_inject_fling_impulse`) or does the rigid body simply retain
   its instantaneous linear+angular velocity from the constrained
   step?
6. **Off-COM grab inertia**: does Unity/Box2D's mouse-joint at an
   off-COM anchor automatically get the parallel-axis term right?
   (It must — that's the point of using a constraint solver.)
   Confirm this is the structural difference vs our pendulum model.

## Files to touch
None this plan — this is research-only. The implementation plan that
falls out of the research will list real files. If we end up with a
"keep the model, tweak X" outcome, the follow-on plan touches
`tokenpal/ui/qt/physics.py` and the pendulum tests; if we end up
with "rewrite as constraint solver," it touches the same files plus
likely a new `tokenpal/ui/qt/_grab_constraint.py` or similar.

## Failure modes to anticipate
- **Research rabbit hole.** "How do game engines do physics" is a
  semester-long question. Scope the agents tightly to the six
  questions above. If they come back with general physics engine
  surveys, that's a failed pass — re-dispatch with sharper briefs.
- **Citing tutorials, not source.** Unity/Unreal/Box2D docs and
  source are authoritative; random Medium posts and Gamasutra
  threads are not. The agents must cite engine docs or
  decompiled/open source (Box2D is MIT, Bullet is zlib, Chipmunk
  is MIT — all readable). Reject findings backed only by
  third-party blog posts.
- **"You should use a physics engine" answer.** That's not actionable
  for a desktop buddy that's currently 600 lines of Python. The
  research output we want is the *math* of the mouse joint, not
  the recommendation to depend on Box2D. If an agent says "use
  pymunk," push back: extract the constraint math, evaluate
  porting ~50 lines of solver, then decide.
- **Conflating 2D and 3D physics.** Some of the techniques
  (especially ragdoll constraint chains) are 3D-specific and not
  worth porting. Filter for 2D-applicable findings.
- **Findings invalidate get-physical's tuning.** We just shipped a
  numeric tuning pass last week. If the research says "the model
  is wrong, rip it out," we'll have to gracefully throw away that
  work. Acceptable cost; flag it explicitly in the diff so the
  user can weigh it.
- **No clear winner.** Research could come back with "honestly
  what you have is fine, this is just animation taste." That's a
  valid outcome — close the plan, log the answer in memory, move
  on. Don't manufacture a refactor to justify the research.
- **Ragdoll-specific noise.** The user mentioned ragdoll physics
  but our buddy isn't a ragdoll (it's one rigid body). The agents
  should note ragdoll findings only insofar as they apply to a
  single-body grab/fling — most ragdoll work is about
  inter-segment constraints, which we don't have.

## Findings summary (research complete)
Three confirmed structural mismatches between the current pendulum
model and how mainstream physics engines (Box2D, Chipmunk, Unity
`TargetJoint2D`, Unreal `PhysicsHandleComponent`) implement
grab/drag/release of an off-COM 2D rigid body:

1. **Off-COM rotational inertia is implicit in the constraint.**
   Box2D / Chipmunk solve `K = J·M⁻¹·J^T` for the effective mass at
   the anchor; the parallel-axis term `I_grab = I_com + m·r²` falls
   out of the Jacobian's lever-arm rows automatically. Our pendulum
   uses `length = |grab → COM|` for the moment arm but does not
   scale rotational inertia for off-COM grabs. (Box2D issue #521;
   Erin Catto, *Sequential Impulses*, GDC 2006.)

2. **Release is "destroy the constraint, body coasts."** No engine
   adds a separate impulse on release — the body inherits its
   instantaneous `v` and `ω` from the last constrained step. Our
   `_inject_fling_impulse` is double-counting on top of the
   asymmetric tracking torque. (Bitsquid, *Inheriting Velocity in
   Ragdolls*, 2012.)

3. **A single constraint impulse drives both linear and angular
   motion.** The mouse joint applies impulse `P` at the anchor and
   updates `v += m⁻¹·P` *and* `ω += I⁻¹·(r × P)` in one step. We
   split this into separate "wind drag" and "tracking torque"
   forces with a hand-rolled handover gate — structural, not just
   stylistic.

4. **Soft-constraint formulation is ~50 lines.** Erin Catto's
   `γ`/`β` derivation from `ω = 2πf`, `k = mω²`, `c = 2mζω` is the
   canonical primitive (Catto, *Soft Constraints*, GDC 2011) and
   ports cleanly to Python. Tuning would re-parameterize as
   frequency (Hz) + damping ratio (ζ).

Integrator choice (explicit vs semi-implicit Euler) is **not** the
source of the "not quite right" feel — at 60 Hz with damped ω, the
difference is long-term stability, not perceptual. Ragdoll-specific
findings (multi-segment constraint chains) do not apply: buddy is
one rigid body.

## Decision (user, 2026-04-25)
1. **Full mouse joint** — body COM translates freely, not
   pivot-locked. Buddy has `(x, y, θ)` state and `(vx, vy, ω)`
   velocity; cursor is the constraint anchor target.
2. **Structural rewrite** — port Catto's soft-constraint solver
   correctly. No targeted patches, no asymmetric tracking, no
   separate fling impulse, no `spin_fade` gate.
3. **Acceptable to throw away get-physical's tuning.** If the
   rewrite doesn't feel right we throw it away and take the lesson.
   Lesson must be summarized when the implementation plan ships,
   regardless of outcome.

## Done criteria (research-only plan)
- Findings summary above written and approved by user. ✅
- Decision recorded above. ✅
- Follow-on implementation plan drafted at `plans/mouse-joint.md`
  and approved.
- This plan moved to `plans/shipped/` once the follow-on is approved.

## Parking lot
- Equilibrium / rest pose under "full mouse joint" needs a real
  answer in the follow-on plan: does the buddy hang from a head
  pivot when released, return to a home anchor via a second
  constraint, or fall under gravity off the screen? Surface in
  mouse-joint.md, not here.
