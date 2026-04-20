# ASCII Art Phase 3 — Scale-Out Work (active)

Resume plan for the ASCII buddy art system. Phase 1 (grounding + headwear
+ highlight color + cloud classifier) and phase 2 (facial_hair +
body_motif + eye_region + trailing + replace/append modes) have shipped.
This doc is the punch list for everything queued after phase 2 so a
future session can pick up cold.

Full design: `plans/brainstorm/ascii-art-master.md`.
Phase 1 plan: `plans/ascii-art-grounding.md`.
Phase 2 plan: `plans/ascii-art-phase2.md`.

## What's already shipped — context for the next session

Commits in order:
- `36838d8` — VISUAL persona section + `_generate_visual_tells` + classifier grounding
- `730762e` — highlight color + headwear zone + cloud classifier gate
- `b01a440` — tests + hex_to_hue_bucket util
- `c56e18a` — voice modal Haiku checkbox
- `d877267` — replace-mode renderer + facial_hair zone (Ice King beard)
- `134b451` — body_motif zone (BMO screen, Bender chest door)
- `909d436` — eye_region zone (Leela cyclops, Hypnotoad spirals)
- `7b6e7aa` — trailing zone + append mode (Rigby tail, Marceline drift)

### Files that carry the design
- `tokenpal/ui/ascii_zones.py` — the zone catalog. Each zone needs a
  quintet of constants (OVERLAYS, OPTIONS, RUBRIC) + entries in
  `_ZONE_MODES`, `_ZONE_COMPAT`, `_REPLACE_TARGETS` or `_APPEND_OVERLAYS`
  depending on mode.
- `tokenpal/ui/ascii_skeletons.py` — the 8 hand-drawn 14-row templates
  + `render()` that composes prefix/body/suffix.
- `tokenpal/tools/train_voice.py` — `_build_classifier_prompt` splices
  rubric_block per zone, `_parse_classification_json` normalizes zones,
  `_render_skeleton_frames` passes zones to render().
- `tests/test_ascii_zones.py` — per-zone legal/illegal render coverage.

### Zone catalog as of end of phase 2
| Zone | Mode | Options | Supported skeletons |
|------|------|---------|---------------------|
| headwear | prepend | none, crown, hood_with_ears, antenna, halo, wizard_hat, spikes | all 8 |
| facial_hair | replace | none, beard_long, beard_stubble | humanoid_tall, mystical_cloaked |
| body_motif | replace | none, screen_dpad, chest_door | robot_boxy |
| eye_region | replace | none, single_cyclops, oversized_spiral | humanoid_tall, animal_quadruped |
| trailing | append | none, tail_curly, hair_drift | animal_quadruped, ghost_floating |

Total configuration space is already past the "one per character" mark
— 4k+ unique renders across the 8 skeletons.

---

## Milestone 3a — ZoneSpec refactor (1-2 hours)

**Why now:** Five zones have been built with the same five-constants-per-
zone pattern. Each new zone requires editing 5 lockstep dicts across
`ascii_zones.py` plus a splice in the classifier prompt. Real duplication
confirmed by three /simplify rounds; deferred through phase 2 because
the shape wasn't stable. Now it is.

**What to do:**
1. Extract a `ZoneSpec` dataclass in `ascii_zones.py`:
   ```python
   @dataclass(frozen=True)
   class ZoneSpec:
       name: str
       mode: ZoneMode  # "prepend" | "replace" | "append"
       # For prepend: option -> template
       # For replace/append: option -> skeleton -> template
       overlays: dict
       rubric: dict[str, str]
       compat: dict[str, set[str]]
       # Replace-mode only: (option, skeleton) -> (start, end) rows
       targets: dict[tuple[str, str], tuple[int, int]] = field(default_factory=dict)

       @property
       def options(self) -> tuple[str, ...]:
           return tuple(self.overlays.keys())
   ```
2. Define one `ZoneSpec` per existing zone (HEADWEAR, FACIAL_HAIR,
   BODY_MOTIF, EYE_REGION, TRAILING). Keep the module-level OVERLAYS /
   OPTIONS / RUBRIC constants as backward-compat aliases (`HEADWEAR =
   ZoneSpec(...)`, `HEADWEAR_OVERLAYS = HEADWEAR.overlays`, etc.) so
   existing imports in tests and train_voice don't break.
3. Auto-derive `_ZONE_MODES`, `_REPLACE_OVERLAYS`, `_REPLACE_TARGETS`,
   `_APPEND_OVERLAYS`, `_ZONE_COMPAT` by iterating a master `_ZONES:
   list[ZoneSpec]` list instead of re-stating each one by hand.
4. Add a helper like `ZoneSpec.rubric_block_for_prompt(self) -> str`
   so the classifier prompt can do `spec.rubric_block_for_prompt()`
   instead of `rubric_block(HEADWEAR_RUBRIC)`.
5. Update tests as needed (shouldn't change the test behavior — just
   the derivation paths).
6. Suite must stay green: `1555 passed`.

**Risk:** Backward-compat aliases are essential — many tests import
`HEADWEAR_OVERLAYS` / `FACIAL_HAIR_RUBRIC` / etc. by name. Break those
and the commit fails on test collection.

**Acceptance:** Adding a hypothetical 6th zone is a single `ZoneSpec(...)`
definition + append to `_ZONES`, not five dict edits.

---

## Milestone 3b — New skeletons: blob_amorphous + hand_creature

**Why:** The superfan brainstorm flagged a ~30% coverage gap for non-
humanoid, non-creature characters. Lumpy Space Princess and Hi Five
Ghost are the canonical test cases; talking food (Peppermint Butler,
Cinnamon Bun) falls into the blob category.

### blob_amorphous
- Irregular, bumpy outline — no clean geometry. Think a purple cloud
  silhouette with lumpy edges using `▆▇▆▇` or similar variation.
- 14 rows, CELL_WIDTH=29, standard palette slots.
- Sample palette: purple + yellow star accent (LSP).
- Compat: `headwear` (crown allowed — LSP wears one), `facial_hair`
  (none only), `body_motif` (none only), `eye_region` (none, maybe
  single_cyclops later), `trailing` (none).

### hand_creature
- Five-fingered hand silhouette in palm-forward orientation (Hi Five
  Ghost, Thing, Rayman-style). Pick ONE canonical orientation; don't
  try to serve multiple.
- 14 rows. Fingers occupy top third, palm middle, legs/base bottom.
- Sample palette: white (Hi Five Ghost).
- Compat: all zones default to `{"none"}` for v1. Iterate if a
  character needs more.

**Work:**
1. Draw both templates. Preview via `.venv/bin/python -m tokenpal.ui.
   ascii_skeletons` and iterate.
2. Add entries to `SKELETONS` dict + `_SAMPLE_PALETTES` (remember
   `highlight` key) + every zone's `_ZONE_COMPAT[zone][skeleton] =
   {"none"}` block.
3. Update classifier prompt's template-list block in
   `_build_classifier_prompt` with 2 new lines (blob_amorphous: LSP /
   talking food; hand_creature: Hi Five Ghost / Thing).
4. Add skeleton entries to test coverage loops so
   `test_every_legal_skeleton_zone_combo_renders_without_exception`
   exercises them.
5. Smoke test: run classifier on "Lumpy Space Princess" + "Hi Five
   Ghost" (after adding them as local voices or passing manual
   classifications) and confirm the silhouettes read.

**Acceptance:** LSP renders as a lumpy purple blob with a crown. Hi
Five Ghost renders as a white hand with a face.

---

## Milestone 3c — Extend VISUAL persona prompt for new zones

**Why:** `_generate_visual_tells` currently asks for appearance in
generic terms. With 5 zones live, the Haiku/Qwen3 recall pipeline
needs steering toward the specific details zones care about: beard
shape, eye count/treatment, body-front motif, tail/drift.

**What to do:**
1. Edit the prompt in `_generate_visual_tells` (tokenpal/tools/
   train_voice.py) to include a zone-hint rubric: "If the character
   has a beard, describe its length and shape. If the character has
   unusual eyes (one eye, spiral eyes, visor), call that out. If the
   character has a distinctive chest element (screen, door, symbol),
   mention it. If the character has a tail or trailing element,
   describe its shape." One sentence per axis.
2. Lower temperature (already 0.3/0.2) and length cap (already 400)
   stay. The goal isn't a longer VISUAL — it's a VISUAL that surfaces
   the details the classifier needs.
3. Smoke-test on Leela, Hypnotoad, BMO, Rigby — the characters whose
   signature tells live in zones other than headwear. Compare VISUAL
   before vs after.
4. Document in the prompt that "UNKNOWN" is still a valid response
   for characters the model doesn't recognize (don't let the expanded
   rubric pressure the model into hallucinating beards).

**Acceptance:** Running the full smoke test (`finn`, `bmo`, `bender`,
`jake`, `mordecai`) produces VISUAL fields that mention the zone-
relevant details when applicable, and the classifier then picks the
right zones more reliably.

---

## Milestone 3d — Mood-aware frame variants (biggest lever)

**Why:** The current 3 frames (idle / idle_alt blink / talking) are
generated from one classification and never change. PersonalityEngine
already tracks 6 moods per character. If each mood could pick a frame
variant, the same 3 frames yield ~18 character-states at zero extra
LLM cost.

This is the single biggest unused payoff in the plan. Superfan
explicitly called it out: "grumpy Bender = slit eyes, smug Rick =
smirk mouth."

**What to do:**
1. Read `tokenpal/brain/personality.py` to understand how moods are
   currently selected and plumbed to the UI.
2. Add a `mood_frames: dict[str, dict[str, str]]` field on VoiceProfile
   that maps mood_name → {"eye": "...", "mouth": "...", "frame_mod":
   "..."} — per-mood eye/mouth overrides.
3. Extend the classifier (or better: a SEPARATE lightweight LLM call
   at train time) to generate per-mood glyph swaps given the persona's
   mood list. Keep it local-only for cost reasons.
4. Update `_render_skeleton_frames` or add a sibling
   `_render_mood_frames` that takes a mood and returns idle/idle_alt/
   talking using the mood's overrides.
5. Wire into the UI: when PersonalityEngine switches mood, call the
   mood-aware renderer and post new frames via `overlay.show_frame`.
6. Add running-bit opportunity: the mood-frame change itself is a
   visible micro-change that the commentary system can reference
   ("your eyes are slits right now, buddy").

**Risk:** This is bigger than any prior milestone. It touches brain +
UI + voice-training paths. Break it into sub-commits:
  - (a) add `mood_frames` schema to VoiceProfile + migrations
  - (b) generate mood_frames at train time (one LLM call)
  - (c) render path accepts mood parameter
  - (d) UI switches frames on mood change

**Acceptance:** When Bender is in a grumpy mood, his eye glyph changes
from ◉ to a slit. The buddy panel re-renders automatically.

---

## Milestone 3e — More beard variants (on-demand)

User explicitly deferred this in phase 2. Do NOT build speculatively —
only when a specific character training session fails to render
correctly due to a missing beard style.

Candidates if/when needed:
- `beard_goatee` — pointy chin beard, humanoid_stocky (Muscle Man's
  dad joke, goatees generally)
- `mustache_thick` — thick horizontal under the nose (Gumball, Mario)
- `beard_wide` — wider/rounder than beard_long (Hermes, Santa)
- New skeletons for beards: add `humanoid_stocky` support when a
  stocky bearded character (Muscle Man's dad, Brian Griffin's dad)
  fails.

**Do this in response to user feedback, not proactively.**

---

## Milestone 3f — Unified lighting pass across skeletons

**Why:** The retro artist's brainstorm called out that every skeleton
currently has its own shading direction — the roster reads as "eight
art styles" instead of "one coherent set." Unifying lit-from-upper-
left would make voices feel like siblings.

**What to do:**
1. Audit each of the 8 SKELETONS templates. Identify where {shadow} is
   currently placed.
2. Standardize: shadow on lower-right edges of each rounded form,
   highlight on upper-left edges. Use `{highlight}` (already exists)
   on one accent cell per row top.
3. Update `_SAMPLE_PALETTES` `highlight` values so each skeleton's
   sample looks right with the new lighting.
4. Re-render all 8 `_SAMPLE_PALETTES` examples in the module preview
   and verify cohesion.

**Risk:** Low — pure art iteration. No logic changes.

**Acceptance:** Running `.venv/bin/python -m tokenpal.ui.ascii_skeletons`
produces a preview where all 8 skeletons clearly share a light
direction.

---

## Execution order recommendation

1. **3a (ZoneSpec refactor)** — do this FIRST. Everything else touches
   zones and will be cleaner after the refactor. Single commit.
2. **3c (VISUAL prompt extension)** — cheap, high leverage. Improves
   the smoke-test results for free.
3. **3b (new skeletons)** — expands coverage once the framework is
   stable.
4. **3f (unified lighting)** — polish pass.
5. **3d (mood-aware frames)** — the big one. Schedule for a dedicated
   session.
6. **3e (more beards)** — reactive, not planned.

## What's genuinely out of scope indefinitely

These were in the original brainstorm but rejected on deeper analysis:
- **Texture slots** (stripes/spots/checker on outfits) — gemma4
  hallucinates on this even with visual grounding. Cloud-only at best.
- **Pose variants** (lean, hand_raised) — multiplies template matrix
  exponentially for low recognition gain.
- **Braille-pixel body regions** — font alignment breaks on many
  terminals.

## Success criteria for Phase 3 complete

- ZoneSpec refactor lands; adding a zone is a single dataclass edit.
- LSP and Hi Five Ghost render as recognizable blob / hand silhouettes.
- Smoke test (`finn` / `bmo` / `bender` / `jake` / `mordecai`) lifts
  from "2 of 5 recognizable locally" to "4 of 5" on Qwen3-14B alone
  (the VISUAL prompt extension should close the gap for Jake/Mordecai
  without touching cloud).
- Mood-aware frames demonstrated on at least one voice (Bender
  grumpy vs cocky).
- Full test suite stays green throughout — target is 1600+ passed by
  end of phase 3.

## Quick start for next session

```bash
cd /Users/smabe/projects/windoze
git log --oneline -8                            # confirm last phase-2 commit is 7b6e7aa
.venv/bin/pytest tests/test_ascii_zones.py -q   # should be 60+ tests passing
.venv/bin/python -m tokenpal.ui.ascii_skeletons # preview all 8 skeletons
# Open the relevant files to refresh context:
#   plans/ascii-art-phase3.md  (this file)
#   tokenpal/ui/ascii_zones.py
#   tokenpal/tools/train_voice.py
```

Reference: `plans/brainstorm/ascii-art-master.md` for the full design
discussion (retro artist + LLM expert + superfan personas) that
everything here traces back to.
