# Environment-aware buddy (movement + weather + body reactions)

## Goal
Make the buddy feel alive in a small physical environment, **Textual overlay only**: he slides continuously around the buddy panel, weather props (sun, rain cloud) ride along based on real local weather, a lightweight particle system reacts to weather (rain droplets, snowflakes, dust motes, lightning) and steam rises off the buddy when it's hot outside. Visuals only — no new commentary plumbing.

## Non-goals
- No new senses. Reuse `weather` only. Hardware sense is NOT consumed by this layer.
- No CPU-pegged reactions (no sparks, no CPU-driven sweat). This box never gets pegged so the trigger would never fire.
- No new LLM prompts, no new chat-log content, no commentary changes.
- No game engine. Physics is per-particle Newtonian only (pos/vel/accel/lifetime); no collision detection between particles or vs the buddy.
- No sub-cell positioning — particles and buddy round to the terminal grid each render.
- No animation in the bordered/borderless speech bubble layout — buddy frame area only.
- No persisting position/state across restarts.
- No more than ~10 fps render tick — terminal flicker risk above that.
- Cap particle count globally (~50). No infinite emitters.
- No overlay-side polling of brain state. State is pushed via a single `UpdateEnvironmentState` message; overlay does not reach into the brain.

## State propagation contract
- New `UpdateEnvironmentState` Message in `tokenpal/ui/textual_overlay.py`. Fields:
  - `weather_reading: SenseReading | None`
  - `idle_event: str | None` (e.g. `"sustained"`)
  - `sensitive_suppressed: bool`
- Posted from `tokenpal/app.py`'s brain-loop tick at ~1 Hz (mirrors the existing `status_callback` cadence).
- Overlay buffers the latest snapshot on a single attribute. The 10 Hz particle simulator reads that buffered snapshot — never the brain.

## Files to touch
- `tokenpal/ui/textual_overlay.py` — buddy widget keeps current behavior; add new sibling `ParticleOverlay(Widget)` at a higher CSS layer (`layers: base particles`). `ParticleOverlay` overrides `render_line(y) -> Strip`. New ~100ms `set_interval` lives on the overlay widget. Buddy widget itself must NOT re-render on a particle tick. Add `UpdateEnvironmentState` Message + handler that updates the overlay's buffered snapshot.
- `tokenpal/app.py` — post `UpdateEnvironmentState` on each brain-loop tick (alongside the existing status callback). Carries the current weather `SenseReading`, current idle `data["event"]`, and a `sensitive_suppressed` bool derived from the same path the brain uses.
- `tokenpal/ui/ascii_props.py` (new) — static sprite frames for sun + rain cloud anchors, plus the particle glyph palettes (rain, snow, steam, lightning, dust). Hex colors only (no Rich-only color names — see markup-healing failure mode). Build `Style` objects at import time and reuse — `Style.parse` is the hot-path expense.
- `tokenpal/ui/buddy_environment.py` (new) — pure-logic module, no Textual imports:
  - `EnvState` (weather kind, intensity, hot_outside flag, afk_active, sensitive_suppressed)
  - `wmo_to_kind(code: int) -> tuple[Kind, float]` — maps the weather sense's `data["weather_code"]` to `(kind, intensity)` since the sense exposes neither field directly.
  - `BuddyMotion` (continuous x/y, target x/y, easing — slides toward target, picks new target every N seconds)
  - `ParticleField` (list of `Particle(x,y,vx,vy,ax,ay,life,glyph,color)`, `tick(dt, panel_w, panel_h, env)` advances + spawns + culls, capped at PARTICLE_LIMIT=50)
  - All deterministic-with-seed for tests; accepts an injected `random.Random`.
  - Brief comment at the top pointing at `davep/textual-canvas` (render_line + Strip pattern) and `asciimatics`'s ParticleSystem class hierarchy as references.
- `tests/test_buddy_environment.py` (new) — table-driven tests on `wmo_to_kind`, `EnvState` selection, and `ParticleField` (seeded RNG: spawn rate scales with intensity, particles cull when off-panel, cap respected, AFK slows spawn rate, sensitive freezes everything).
- `CLAUDE.md` — short note under "UI" about the environment layer + particle system once shipped.

## Failure modes to anticipate
- **Layout pressure**: buddy frame already has dynamic min-width logic and a bubble-suppression cascade; particles must render *inside* the existing buddy panel area, not extend it.
- **Tick-rate flicker**: existing 0.03s typing animation + 4s blink interval are already on the loop. The 100ms simulator tick lives on the sibling overlay widget so the buddy's own widget does not re-render every tick. Verified by instrumenting `BuddyWidget.update` calls during particle activity.
- **Particle cost on slow terminals**: 50 particles × 10 fps × Rich-markup string assembly can chew CPU on iTerm with GPU rendering off. Profile early; downgrade fps or cap before merging.
- **Markup brittleness**: Rich markup healing in `ascii_renderer._fix_markup` exists for a reason — any new sprite or particle glyph that uses Rich-only color names will crash Textual. New props must use hex colors from the start.
- **Style.parse cost**: cache `Style` objects at import time in `ascii_props.py`; do not call `Style.parse` per-particle per-tick.
- **Layered widget removal artifacts**: Textual #2076 — explicit `refresh()` after any structural change to the layered overlay.
- **Weather sense is opt-in**: most users won't have `/zip` set, so the default experience must be "no prop, no weather particles, buddy just slides a little + ambient dust." No errors, no blank space artifacts.
- **Weather has no enum/intensity field**: sense exposes `data["weather_code"]` (WMO int) + `condition` (string lookup). `wmo_to_kind` is the bridge — write the table once, test it.
- **Sensitive-app suppression is not pushed today**: `app.py` must derive the bool from the same path the brain uses (`personality.check_sensitive_app(snapshot)`) and include it in `UpdateEnvironmentState`. Confirm thread safety of that read before wiring.
- **AFK proxy**: subscribe to the `idle` sense's `data["event"]`; treat `"sustained"` as AFK. Don't try to replicate the multi-condition composite — the sustained flag is enough for movement scaling.
- **AFK behavior**: movement slows to near-zero, particle spawn rate drops, but particles continue to fall (rain still falling looks right; buddy pacing does not).
- **Sensitive behavior**: buddy freezes, particles freeze in place.
- **Prop occluding the buddy face**: rain cloud above head must clear the top of the tallest frame; sun off to the side must not collide with the speech-bubble tail anchor.
- **Particle vs buddy overlap**: buddy must win the cell (z-order: buddy > props > particles). Sibling overlay layer is above, but `render_line` returns `Strip.blank` for cells the particle field doesn't own, so the lower layer (buddy) shows through.
- **Voice ASCII frames vary in size**: each voice has its own idle/idle_alt/talking dimensions. Movement bounds and prop anchoring must be computed from `BuddyWidget.max_frame_width()` at tick time, not a hardcoded one. Re-measured on voice switch.
- **Panel resize mid-simulation**: terminal resize changes panel_w/panel_h. Particles outside new bounds must cull cleanly; buddy target must clamp to new bounds without teleporting visibly.
- **Random determinism in tests**: particle spawn uses RNG; `ParticleField` must accept an injected `random.Random` so tests are reproducible.

## Done criteria
- With no weather configured and a quiet machine, the buddy slides smoothly between targets within his panel (no teleporting), picks new targets every ~5-15s, and ambient dust motes drift across the panel. No crashes, no console warnings, no flicker on a default Terminal.app / iTerm window.
- With `/zip` set to a sunny location (or stubbed sunny reading), a small sun sprite renders adjacent to the buddy and persists across his movement.
- With weather stubbed to rain, a rain-cloud sprite tracks the buddy's position AND raindrops fall through the panel at intensity-scaled rate. Stubbed snow → snowflakes drifting with sine-wave horizontal drift. Stubbed storm → occasional lightning flash glyph.
- With outdoor temp above threshold (stubbed), sweat-bead glyphs render on/near the buddy's head and steam particles rise. Clears within ~1s after temp drops below the threshold.
- Sustained-idle (idle_event == "sustained"): buddy stops sliding (or slows to crawl); particles continue but spawn rate drops. Sensitive-app: buddy freezes, particles freeze in place. Both verified by manual run + unit test on `buddy_environment`.
- Particle cap (~50) is never exceeded under any stub scenario — verified in test.
- z-order verified manually: buddy never gets visually covered by his own raindrops.
- Buddy widget never re-renders on a particle tick — verified by instrumenting `BuddyWidget.update` calls during a 10s particle-active run.
- `pytest tests/test_buddy_environment.py` green; full suite still green.
- `ruff check tokenpal/` and `mypy tokenpal/ --ignore-missing-imports` clean for touched files.
- One short paragraph added to CLAUDE.md under "UI" describing the environment layer + particle system + where to extend it.

## Parking lot

### Layout option A — bubble in its own fixed region (CHOSEN after option B retry)

```
┌────────────────────────────────────────────────────┐
│ Header                       height: 3             │
├────────────────────────────────────────────────────┤
│                                                    │
│ ParticleSky                  height: 1fr           │  sun/moon, particles, cloud
│                                                    │
├────────────────────────────────────────────────────┤
│ SpeechBox                    height: 6 (fixed)     │  always present, empty
│                                                    │  when no bubble
├────────────────────────────────────────────────────┤
│ BuddyArea                    height: 14 (fixed)    │  buddy art, slides via offset
├────────────────────────────────────────────────────┤
│ Input                        height: 3             │
├────────────────────────────────────────────────────┤
│ Status                       height: 1             │
└────────────────────────────────────────────────────┘
```

Pros: dead simple, no layered widgets, no z-order math, no risk of transparency
bugs. Every region is opaque and lives at a fixed slot — pure flat composition.

Cons: 6 lines of dead space when no bubble is up. The cloud at the bottom of
the sky has a visible 6-row gap to the buddy below — not "directly above his
head" the way option B achieves.

### Layout option B — bubble layered ON TOP of buddy in one Stage (TRIED, REJECTED)

Tried during implementation; bubble's variable height could overflow the
stage and clip the buddy's head when the bubble was tall. Increasing stage
height to fit both removed the "cloud directly above buddy head" benefit
(cloud-to-buddy gap = stage_height - buddy_height = same as option A's
SpeechBox region). No real win over A; reverted.

```
┌────────────────────────────────────────────────────┐
│ Header                       height: 3             │
├────────────────────────────────────────────────────┤
│                                                    │
│ ParticleSky                  height: 1fr           │  sun/moon, particles
│                                                    │  cloud anchored at bottom
│                                                    │  → directly above buddy
├────────────────────────────────────────────────────┤
│ Stage                        height: ~18 (fixed)   │
│   layers: base bubble                              │
│   ├ BuddyWidget   layer: base, dock: bottom        │  buddy always visible
│   └ SpeechBubble  layer: bubble, dock: top         │  bubble pops over buddy
│                   (opaque bg, no transparency)     │  area when active
├────────────────────────────────────────────────────┤
│ Input                        height: 3             │
├────────────────────────────────────────────────────┤
│ Status                       height: 1             │
└────────────────────────────────────────────────────┘
```

Pros: cloud is truly just above buddy (no gap). Bubble pops over his head when
he speaks. No region reflow when bubble appears.

Cons: uses Textual `layers:` (which bit us once on the transparency path).
Mitigation: bubble is OPAQUE — no `Strip.blank` transparency required, so the
class of bug we hit before doesn't apply. Both children get explicit `layer:`
assignments per the docs.
