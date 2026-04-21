# reactplus-ii — particles on the buddy himself

## Goal
Get the dizzy swirl (and later the impact burst, steam, possibly weather) to render **in the buddy's own widget region** so reactions visibly hug him, track him as he slides, and can layer on top of his ASCII art. Side-effect: it'll interact with the text visually — particles can overlap the buddy's body glyph-by-glyph, which is the thing we want to see.

## Non-goals
- No CSS `layers:` based transparency tricks. Smoke-tested — Textual doesn't do per-cell compositing; an upper-layer widget's region wholly blocks the lower layer regardless of its declared background. Plan lives in the git log at `plans/shipped/reactplus.md` explorations and the `/tmp` smoke scripts.
- No reparenting ParticleSky or BuddyWidget. The widget tree stays the same.
- No new layers in tcss.
- Not expanding `BuddyStage`'s hitbox or mouse handling. That's locked in from reactplus-i.
- Not rewriting weather. Weather stays in `ParticleSky` and looks the same. Only reaction particles (swirl + impact + steam) move into the buddy's space.
- No per-voice reaction lines. Still parking-lot.
- No window-move detection. Still parking-lot.

## Architecture decisions

**Shared ParticleField at the overlay level.** Today `ParticleSky` owns both `BuddyMotion` and `ParticleField` and runs the tick. Move both into a new `BuddyEnvironmentController` held by `TokenPalApp` (accessible to all panel widgets). Field uses **panel-relative coordinates** — (0, 0) is the top-left of `#buddy-panel`. Every widget that wants to render particles reads them from the shared field and converts panel-y → widget-local-y for its own `render_line`.

**Who renders what:**
- `ParticleSky.render_line`: weather particles (rain, snow, stars, lightning, steam), renders any particle whose panel-y falls in the sky's panel-y range.
- `BuddyWidget.render_line` (NEW override): first renders the ASCII art (via `super().render_line(y)`), then overlays any particle whose panel-y falls in the buddy's panel-y range on top. Cell-level overwrite — a particle glyph replaces the buddy character at that cell. User explicitly wants to see this interaction.
- Speech bubble, input, status bar: no particle rendering. If a particle drifts out of both the sky's and buddy's rows, it's culled or invisible — acceptable.

**Who runs the tick:**
- `TokenPalApp` gets a 10 Hz `set_interval` that calls `env.tick()`. The app is the only widget that can cleanly see panel dims + buddy position. ParticleSky's current tick moves into the env.
- Env also writes `buddy.styles.offset` (slide) and `#buddy-stage.styles.offset` (drag) — same as today. Nothing changes in those writes; they just live in a different class now.

**Coordinate math:**
- `panel = self.query_one("#buddy-panel", Vertical)`; `buddy = self.query_one(BuddyWidget)`.
- Buddy's panel-y range: `buddy.region.y - panel.region.y` through `+ buddy.region.height`.
- Particle spawn anchors (dizzy, impact, steam) use these panel-y values directly.
- ParticleSky's sky-y range: `sky.region.y - panel.region.y` through `+ sky.region.height`.
- When rendering, each widget converts panel-y to widget-local-y by subtracting its own `region.y - panel.region.y`.

**Interaction with ASCII art** (the interesting part the user wants to see):
- Default: particle glyph fully replaces the buddy cell at that column. The buddy "wears" the particle briefly.
- If readability suffers (the buddy ends up peppered with rain characters when a storm hits), phase 5 adds a `text-opacity: 65%` on particle segments so they feel ghostly against the underlying ASCII.
- Dizzy swirl is the target case — a handful of purple glyphs orbit around/on the buddy's head for 3s. Impact burst is the other — 5 yellow glyphs radiate out from the click point.

## Files to touch
- `tokenpal/ui/buddy_environment.py` — extract the tick owner. Add a `BuddyEnvironmentController` class that owns `BuddyMotion` + `ParticleField` and has a `tick(dt, panel_w, panel_h, buddy_panel_y_top, buddy_panel_y_bottom, buddy_panel_x_center, env)` method. ParticleField coord space stays the same (arbitrary x/y floats) but anchors get interpreted as panel-relative. Spawn methods that currently use `panel_h` as the buddy-y anchor get updated to take an explicit buddy-y param.
- `tokenpal/ui/textual_overlay.py` — big one:
  - `TokenPalApp.on_mount` instantiates the env controller. `_sim_tick` moves onto the app (runs at 10 Hz). Calls `env.tick(...)` and `env.apply_widget_offsets(buddy, stage)`.
  - `ParticleSky` stops owning `_motion`/`_field`/`_cloud_drift`. It reads state from the env. `_sim_tick` becomes a pure render-refresh (or stays as a read-only accessor). `render_line` converts panel-y to sky-local-y and queries `env.field.particles`.
  - `BuddyWidget` gets a `render_line` override that delegates to `super().render_line(y)` for the ASCII art, then overlays particles whose panel-y falls in the buddy's region. Needs access to the env — via `self.app.env` (app attribute) or passed in via a callback.
  - `BuddyStage.bind_motion` → `bind_env` (takes the controller).
  - `env.apply_widget_offsets` preserves the slide + drag offset plumbing (buddy and stage offsets) that ParticleSky does today.
- `tokenpal/app.py` — wire the env controller into the app: create on overlay setup, pass to TokenPalApp init. No brain changes (brain still uses `on_buddy_poked` / `on_buddy_shaken` the same way).
- `tests/test_buddy_environment.py` — extend: `BuddyEnvironmentController` tick coordinates; panel-relative spawn anchors; shared tick outputs still match single-ParticleSky behavior.
- `tests/test_ui/test_textual_overlay.py` — smoke: particles spawned at buddy's row render inside `BuddyWidget`, not `ParticleSky`.
- `tests/test_ui/test_textual_overlay_physics.py` — update the existing pilot tests: stage binding now goes through the env controller.

## Phases (each is a separate commit)

**Phase 1: Hoist state into a `BuddyEnvironmentController`.**
ParticleSky keeps driving the tick; but `BuddyMotion`/`ParticleField`/`CloudDrift` move into the controller, owned by the app. ParticleSky reads from the controller. No user-visible change. Existing tests pass unchanged.

**Phase 2: Move the tick owner from ParticleSky to the app.**
`TokenPalApp._sim_tick` runs the 10 Hz loop. ParticleSky becomes a read-only renderer. Offset writes (buddy slide, stage drag) move onto the env controller. BuddyStage's `bind_motion` becomes `bind_env`. Still no user-visible change.

**Phase 3: Switch field coordinates to panel-relative.**
All spawn anchors (steam, rain, snow, impact, swirl, prop-follow-buddy) compute panel-relative Y from widget regions. ParticleSky's render_line converts panel-y → sky-local-y. BuddyWidget render_line is NOT touched yet; particles outside sky rows go invisible temporarily. User-visible effect: only the dizzy swirl + impact burst become invisible (they were spawning at sky rows; now they spawn at buddy rows, which no widget renders yet). Transient phase — phase 4 fixes it.

**Phase 4: BuddyWidget `render_line` overlays particles on the ASCII art.**
Delegate to `super().render_line(y)`, then walk particles in the buddy's panel-y range and overwrite specific cells with particle glyphs. Dizzy swirl now appears on the buddy's head (tracking him). Impact burst visible on the buddy's body.

**Phase 5: Tune spawn anchors + readability.**
Dizzy swirl spawns at `buddy.panel_y_top + 1` so glyphs sit on the head cells specifically. Impact burst spawns at buddy center. Steam (hot weather) re-anchors to buddy's top instead of bottom of sky. Add `text-opacity: 65%` on particle segments if the ASCII art readability suffers (spike-test it first).

**Phase 6: Tests + ruff + mypy + manual smoke + commit.**

## Failure modes to anticipate
- **BuddyWidget `render_line` performance**: `Static.render_line` is called once per visible row per refresh. Walking `len(particles) ≤ 80` per row is O(rows × particles) per refresh — 10 rows × 80 = 800 checks per refresh at 10 Hz = 8k/sec. Fine.
- **ASCII art cell occlusion unpleasant**: particle glyphs replacing buddy face cells (the eyes, mouth) could produce awful visuals. Mitigation in phase 5 — either text-opacity or skip cells whose buddy character isn't a space. Default to the latter (particles only render in "empty" cells of the ASCII art) — preserves the face.
- **Region queries pre-mount**: `buddy.region` / `panel.region` may be `(0, 0, 0, 0)` before mount. Guard with a "not ready" check in the controller tick.
- **Brain status bar or input field getting particle glyphs**: guarantees that particle-renderer widgets are ONLY BuddyWidget and ParticleSky. Don't add render_line to anything else. Any particle whose panel-y falls in rows between sky bottom and buddy top (i.e., inside the speech region) is just invisible — that's fine.
- **Speech bubble over the buddy's head**: speech region sits above the buddy. If swirl spawns at buddy's top row, it could render JUST BELOW the speech bubble. Usually fine — rendered through BuddyWidget at its top padding.
- **Test isolation**: pilot tests from reactplus-i bind `sky.motion` via BuddyStage. After this refactor, binding goes through the env. Need to update those two lines.
- **ParticleSky's tick also writes `speech_region.styles.offset`**: that plumbing moves into the env's `apply_widget_offsets` method. One more place to verify nothing breaks (bubble still rides along horizontally with the buddy).
- **Populate_starfield re-signature**: the starfield re-populates when sky dims change. Still works — starfield lives at the top of the panel (sky rows), unchanged by the coordinate refactor since sky-panel-y range == sky's own rows when sky is at the top of the panel.
- **Sensitive-app suppression**: env controller's tick must honor `env.sensitive_suppressed` same as today. Unchanged logic, different owner.

## Done criteria
- Dizzy swirl renders **inside BuddyWidget's region** (on or near the head) and tracks the buddy's horizontal slide.
- Impact burst on click renders at the buddy's body (visible radial fan) rather than in the sky.
- Weather still renders in the sky region exactly as before.
- Buddy's slide + stage drag offsets still work.
- Speech bubble still rides along with the buddy.
- `pytest tests/test_buddy_environment.py tests/test_ui` all green.
- `ruff check tokenpal/` clean (no new issues).
- Manual smoke: clicking and shaking the buddy visibly interacts with his body glyph.

## Parking lot
- Render particles in speech-region too (so swirl can drift up into the bubble area).
- `text-opacity` on particle segments if the overlay-on-ASCII readability needs softening.
- Weather (rain) visible across the buddy body (currently stops at the sky boundary). Enable by adding BuddyWidget.render_line query for weather-kind particles. Scope creep; keep for later.
- Per-voice canned reactions.
- Platform-specific window-move detection.
