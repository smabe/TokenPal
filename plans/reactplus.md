# reactplus — physical reactions for the buddy

## Goal
Make the buddy feel physically present in the terminal: he reacts when clicked, can be grabbed and dragged around the buddy panel, protests when shaken. Introduces a `#buddy-stage` wrapper around `BuddyWidget` so drag and slide offsets compose on separate planes instead of fighting for `buddy.styles.offset`.

## Non-goals
- No OS-level drag of the Textual app window itself (terminals own that).
- No reactions to the terminal window being moved. Inside a TUI we only see `on_resize`; real window-move detection needs a platform-specific daemon (Quartz / pygetwindow / xdotool) and belongs in its own plan. Parking lot.
- No physics engine — we want hand-tuned reactions, not a particle-accurate simulator.
- No persistence of buddy position across restarts.
- No new LLM prompts for reactions beyond a short canned line routed through the existing high-signal bypass path (click/shake are events, not conversation).
- No per-voice canned reaction lines in this plan — ship a global fallback dict first. Per-voice training integration is parking-lot.
- No CSS `layers:` directive — ParticleSky stays a sibling of `#buddy-stage` inside `#buddy-panel`, so DOM order handles render stacking. (We cannot move ParticleSky into the stage: it reads `self.size.width` as panel-relative for slide-bounds math.)
- Not rewriting `buddy_environment.py`'s existing slide logic — physics state layers ON TOP via new fields on `BuddyMotion`, not replacing `EnvState`/`EnvironmentSnapshot`.
- No mouse tracking outside the buddy stage (no "buddy looks at your cursor" — separate idea, parking-lot).

## Architecture decisions (set before coding)

**Widget tree.** Wrap `BuddyWidget` in a new `Container(id="buddy-stage")` inside `#buddy-panel`. Tree becomes:

```
#buddy-panel (Vertical)
  ├─ HeaderWidget
  ├─ ParticleSky            ← unchanged, sibling of stage
  ├─ #speech-region
  │   └─ SpeechBubbleWidget
  ├─ #buddy-stage           ← NEW
  │   └─ BuddyWidget (id="buddy")
  ├─ Input (id="user-input")
  └─ StatusBarWidget
```

**Offset plane separation (the real win from the stage).** ParticleSky continues to write `buddy.styles.offset` every frame for the ambient slide. Drag writes `#buddy-stage.styles.offset`. The two compose (final rendered position = stage offset + buddy offset) so drag does not fight slide and vice-versa. On drag release, stage offset eases back to (0, 0).

**Physics state lives on `BuddyMotion`** in `buddy_environment.py`. New fields: `recoil_ticks: int`, `drag_offset_x: float`, `drag_offset_y: float`, `velocity_x: float`, `velocity_y: float`, `shake_score: float`, `dizzy_ticks: int`, plus a small rolling buffer of recent drag deltas for direction-reversal counting. `BuddyMotion.tick()` decays them. `EnvState` / `EnvironmentSnapshot` remain untouched.

**Overlay → brain channel** (new pathway — doesn't exist today). `Brain` gets `on_buddy_poked()` / `on_buddy_shaken()` threadsafe enqueue methods that push to a new `_buddy_event_queue` drained by the brain loop, mirroring `submit_user_input`'s pattern. Brain routes events through a high-signal bypass modeled on the git path at `orchestrator.py:605-623`: skips comment-rate gate + interestingness threshold, but STILL honors sensitive-app suppression and `_forced_silence_until`. Verbal reaction is decoupled from visual reaction — the UI recoil/dizzy animation always plays, the verbal riff is gated.

**Canned reactions (global).** New `_BUDDY_REACTIONS: dict[str, list[str]]` constant in `personality.py` keyed by `"poke" | "shake"`. `PersonalityEngine.canned_reaction(kind) -> str | None` picks one at random. No LLM call. Per-voice override deferred.

**Click vs drag disambiguation.** `on_mouse_down` on `#buddy-stage` starts a potential-drag state with start position + timestamp. `on_mouse_move` promotes to drag after either (a) ≥4 cells of total displacement, or (b) 150ms elapsed. `on_mouse_up` before promotion = click; after = drag-end.

**Shake detection.** Sliding 500ms window of signed (dx, dy) per frame. Count sign changes on each axis. ≥3 reversals on either axis within the window → `shake_score` bumps; crossing threshold → dizzy state (3s, swirl glyphs, one riff per event).

**Mouse-capture safety.** `TokenPalApp.on_screen_push` / `on_screen_pop` hooks call `release_mouse()` on the stage widget if it holds capture. Also release on `Resize`.

## Files to touch
- `tokenpal/ui/textual_overlay.py` — wrap BuddyWidget in Container(id="buddy-stage"); add `BuddyStage` class with `on_mouse_down/move/up/click` + `on_leave` + capture-release lifecycle; extend `_sim_tick` to pump physics via the environment's `BuddyMotion`; compose stage + buddy offsets (stage holds drag offset, buddy keeps slide offset); hook `on_screen_push/pop` to release capture.
- `tokenpal/ui/textual_overlay.tcss` — new `#buddy-stage` rule: `width: 100%; height: auto; padding: 0 1;` (the `padding: 0 1` gives a 1-cell click margin on each side of the glyph).
- `tokenpal/ui/buddy_environment.py` — add physics fields to `BuddyMotion`; extend `tick()` to decay recoil/dizzy, integrate drag velocity, update shake rolling window; add `poke()` / `drag_update(dx, dy, dt)` / `release()` entry points; keep pure (no Textual imports).
- `tokenpal/ui/ascii_props.py` — add `Kind.impact_stars` + `Kind.dizzy_swirl`; add `_spawn_impact_stars(x, y, rng)` burst and `_spawn_dizzy_swirl(buddy_x, buddy_y, rng)` in `ParticleField`. Hex colors only.
- `tokenpal/brain/orchestrator.py` — add `Brain.on_buddy_poked()` / `on_buddy_shaken()` threadsafe entry points + `_buddy_event_queue` drain in the main loop; route through a new `_generate_buddy_reaction(kind)` method modeled on `_generate_git_nudge`; respect sensitive + `_forced_silence_until`; cooldown at `_last_buddy_reaction_time` (5s) to defuse spam.
- `tokenpal/brain/personality.py` — `_BUDDY_REACTIONS` dict + `canned_reaction(kind)` getter. No LLM hookup.
- `tests/test_buddy_environment.py` — extend with: click recoil decay, drag offset update, shake-rolling-window + direction-reversal threshold, dizzy timeout, sensitive-suppressed freeze includes physics fields.
- `tests/test_ui/test_textual_overlay_physics.py` — NEW. Uses existing `overlay` + `app` pilot fixtures. Asserts: click fires `on_buddy_poked`, drag writes to `#buddy-stage` offset, screen-push during drag releases capture, shake routes to `on_buddy_shaken`.

## Failure modes to anticipate
- **Mouse-capture leak**: modal opens mid-drag → capture never released → whole app freezes on mouse input. Mitigation: `on_screen_push` + `on_screen_pop` + `on_resize` all call `release_mouse()` when stage is dragging.
- **Drag vs slide fight**: before the stage split, both drag and slide wrote to `buddy.styles.offset`. With the stage split they write to different widgets and compose correctly — but there's still a subtle case: during drag we also want to suppress `BuddyMotion` target-wandering so release doesn't snap weirdly. Solution: `BuddyMotion` enters a "held" state while `drag_offset` is non-zero, target stays pinned to current position, and on release we clear both and let the slide resume from wherever.
- **Shake false positives during normal linear drag**: a fast pan across the panel can look like shake if we only count velocity magnitude. Direction-reversal count (≥3 sign changes in 500ms) filters this.
- **Sensitive-app suppression must still win**: environment already freezes on sensitive apps. Physics reactions must respect that — no particles, no bubble, no brain event. The UI checks `EnvironmentSnapshot.sensitive_suppressed` before enqueuing to `_buddy_event_queue`; the brain's bypass path also re-checks.
- **Textual mouse reliability on Windows Terminal**: mouse events are flaky on some WT configs; iTerm/Ghostty are fine. Must degrade gracefully — if we never see a mouse event, nothing breaks and the buddy just slides as today.
- **Click vs drag disambiguation on flaky mice**: a jittery trackpad could emit a 2-cell mouse_move between down and up that shouldn't count as a drag. 4-cell threshold handles this.
- **Gate flooding via click-spam**: 5s cooldown on verbal reactions + reuse of existing `_comment_timestamps` window cap (8-per-300s) on the brain side. Visual recoil has no cooldown — every click recoils, but only ~1-in-5 clicks speak.
- **Speech bubble mid-drag**: if a bubble is typing when a click lands, we don't interrupt. Recoil still plays, verbal riff gets parked in `_pending_bubble` or dropped based on the existing bubble-queue cap.
- **ParticleSky still owns the frame loop**: ParticleSky's `_sim_tick` writes buddy slide and speech offsets. We must NOT let the physics tick in `BuddyMotion` write to any widget — it only updates pure state. ParticleSky's tick reads that state and writes to widgets. Clean separation must stay intact.
- **`_apply_buddy_panel_min_width` collision**: min-width is set on `#buddy-panel`, not the stage. Confirmed safe. Stage stays `width: 100%` and inherits.
- **Impact-stars + dizzy-swirl respecting the 50-particle cap**: both go through `ParticleField._try_append`, which already enforces `PARTICLE_LIMIT = 50`. Burst sizes tuned small (3-5 stars, 4-6 swirl glyphs) so they don't starve weather particles.

## Done criteria
- Clicking the buddy produces a visible recoil/impact animation (stars burst, buddy briefly jumps) that decays in <1s.
- Clicking the buddy occasionally (≤1 in 5, with 5s cooldown) produces a short canned verbal reaction through the high-signal path. Respects sensitive-app suppression.
- Mouse-down on buddy + drag moves the buddy within `#buddy-panel` bounds via `#buddy-stage.styles.offset`; slide motion stays on `buddy.styles.offset` and does not fight the drag; release eases the stage offset back to (0, 0).
- Shaking the buddy (≥3 direction reversals within 500ms on either axis) triggers dizzy state: swirl glyphs overhead, brief freeze, lasts ~3s, fires exactly one "stop that" canned riff per dizzy event.
- Opening any modal mid-drag releases mouse capture cleanly via `on_screen_push`.
- `pytest tests/test_buddy_environment.py tests/test_ui/test_textual_overlay.py tests/test_ui/test_textual_overlay_physics.py` all green; existing tests unchanged.
- `ruff check tokenpal/` clean, `mypy tokenpal/ --ignore-missing-imports` clean.
- Manual smoke on macOS (iTerm or Ghostty): click, drag, shake, release, modal-open-mid-drag all feel right.

## Parking lot
- Platform-specific window-move detection (Quartz / pygetwindow / xdotool) in a daemon thread → buddy reacts to the terminal window moving.
- Per-voice canned reactions on `VoiceProfile` with `/voice regenerate` producing them.
- Generous "buddy escapes the panel" physics (requires panel overflow or reparenting on release — much bigger scope).
- "Buddy watches the cursor" — eye tracking while the mouse moves inside the panel.
- Ragdoll physics on hard-release throws — stage offset with momentum, bounce off panel walls.
