# ASCII Art Phase 2 — Replace-Mode Zones (active)

Full design: `plans/brainstorm/ascii-art-master.md`. Phase 1 shipped
(headwear + highlight color + cloud-classifier opt-in).

## Scope for this phase
Ship **row-replace mode** renderer + the `facial_hair` zone end-to-end
with a MINIMAL beard set. Expand only if testing says we need more.

## Milestones

### 2a — Replace-mode renderer (1 day)
- Extend `ascii_zones.py` with a richer zone model: per-zone `mode` +
  per-(skeleton, zone) `target_rows` when mode is `replace`.
- Update `render()` in `ascii_skeletons.py` to walk the rendered body
  rows and splice in replace-mode content.
- No new zones yet; prove the plumbing with a test that targets a
  known row range and asserts the row-replace kicked in.

### 2b — `facial_hair` zone, minimal (1 day)
- **Two beard styles only to start**: `none` + `beard_long` (Ice King,
  wizards) + `beard_stubble` (Hank Hill, Pops).
- **Two skeletons only to start**: `mystical_cloaked` + `humanoid_tall`.
- Hand-drawn micro-templates with transparent shoulder edges so the
  body silhouette still peeks past the beard.
- Compat table: beard allowed on humanoid_tall, humanoid_stocky,
  mystical_cloaked; `none` everywhere else.
- Classifier prompt gains beard rubric.
- Smoke test: Ice King with manual VISUAL must read as Ice King.
- `/simplify` + commit. Expand to more beards/skeletons in a follow-up
  ONLY if the smoke test exposes gaps.

### 2c — Persona prompt update (30 min)
- Extend `_generate_visual_tells` to explicitly ask about beard shape
  when relevant. Don't force it — "no beard or tail" is a valid answer.

### 2d — Tests (parallel with 2b)
- `test_render_respects_replace_mode_target_rows` — synthetic zone with
  known target rows, assert body has been rewritten.
- `test_beard_renders_without_exception` for each legal (skeleton,
  beard) pair.
- `test_beard_illegal_on_ghost_floating_coerces_to_none`.
- Golden render for Ice King on `mystical_cloaked` with `beard_long`.

## Deferred to later phases

- `eye_region` zone (Leela / Hypnotoad) — second replace-mode zone, do
  after facial_hair proves the pattern.
- `body_motif` zone (BMO D-pad, Bender chest door) — splice mode, low
  priority until needed.
- `trailing` zone (Rigby tail, Marceline drift).
- `blob_amorphous` + `hand_creature` skeletons.
- Mood-aware frame variants.

## Open questions resolved

- Non-default eye regions will SKIP the blink pipeline (option c from
  the discussion). Spirals pulse instead of blink. Documented here so
  we don't relitigate when eye_region ships.
- Z-order: declare target-row ranges disjoint by construction per
  skeleton. No conflict-resolution logic in the renderer.
