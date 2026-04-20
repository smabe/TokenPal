# Voice-Buddy ASCII Art — Master Plan

## Problem

Current system (8 skeletons × 5-color palette × eye/mouth glyph swap) is a costume-changer, not a character designer. Finn and Mordecai both land on `humanoid_tall` in a blue shirt. Bender and BMO both land on `robot_boxy` in their brand color. Color is doing 100% of the work; silhouette disambiguation is doing 0%. Fix: expand the **addressable zones** a single skeleton exposes so the classifier can hit the signature shapes that make a character read — without ever letting the LLM draw freeform.

## Test model

All iteration + golden fixtures run against **Qwen3-14B-Q4_K_M** on the llamacpp path (`[llm] inference_engine = "llamacpp"`). Qwen3-14B is materially stronger than gemma4 on enum discipline (LLM expert estimated ~20-way enum ceiling vs gemma4's 12), but the smoke-test below shows it still makes catastrophic color choices without visual grounding. Plan targets Qwen3-14B behavior; gemma4 remains supported via the same code paths but will lean harder on the compat table + cloud fallback.

## Smoke test — current-system baseline

Ran `_classify_character_for_skeleton` on 5 existing local voices (`~/.tokenpal/voices/*.json`) on 2026-04-20 using the default local inference stack. Findings:

1. **Skeleton picks are mostly sane.** humanoid_tall for Finn/Mordecai, robot_boxy for BMO/Bender, animal_quadruped for Jake. The 8-way enum holds.
2. **Color picks are catastrophic.** `#8B4513` (saddle brown) appeared in 4 of 5 outputs. Bender came out dark-red + brown + gold. BMO came out gold hair + pink skin + deep blue. Finn came out saddle-brown hair + gold shirt. The classifier is *not* recalling canonical character colors from the fandom URL — it's picking generic RPG/medieval palettes.
3. **Persona cards contain zero visual information.** They are pure VOICE / CATCHPHRASES / NEVER — voice-acting direction, no appearance text. The classifier currently receives this plus the fandom URL host and a character name, and is expected to derive canonical colors from pretraining alone. That's the gap.
4. **6 of 9 voices have `ascii_idle = None`.** The ASCII system is under-deployed AND broken where deployed. Regenerate path matters as much as the classifier fix.

This is the ground truth the plan must improve against. Pass bar for Phase 1: rerun the same 5 voices, get canonical colors on ≥4 of them.

## Consensus across personas

All three agreed on:
1. **Silhouette > color.** Two characters with the same outline can't be saved by a palette swap. (retro, superfan)
2. **Headwear/crown-anchor is the highest-ROI slot.** Ice King's crown, Bender's antenna, Finn's bear-hood, Jake's floppy ears, BMO's nubs, angel halo — all live on the same anchor and cover the largest recognition-gain surface. (retro, LLM, superfan)
3. **6th palette color (`highlight`).** Classic EGA dark/mid/light triad. One extra hex, no new enum, zero combo risk. (retro, LLM)
4. **Every new field is a closed enum with `"none"` fronted.** Validate + silently coerce illegal combos server-side; never retry for semantic errors. (LLM, implicitly retro)
5. **Cloud-path opt-in for the classifier.** `CloudBackend.output_config.format` enforces schema at the SDK layer and eliminates parse-retry entirely. Haiku at ~$0.002/voice is noise. (LLM)

## Unresolved tension → resolution

The retro artist proposed **additive accessories at anchor points** (crown on top, bowtie at neck). The superfan counter-argued that the biggest recognition wins require **regional override** — Ice King's beard displaces body rows, Leela's single eye replaces the two-eye region, Hypnotoad's spirals overrun the whole face. Purely additive covers ~30% of iconic characters; override is needed for the other 70%.

**Resolution**: unify under one concept — **named zones**, each with an optional override. A zone declares a bounding box (rows × cols) on each skeleton. Most accessories are 1-cell splice at a zone's anchor (additive). A few zones allow *replacement content* drawn from a curated enum (beard, oversized-eye, body-motif). The classifier picks zone values; the renderer decides splice-vs-replace based on the zone's type. This keeps the JSON flat and enums small while unlocking the superfan's must-haves.

Texture slots and pose variants were rejected for v1: retro artist wanted them, LLM expert flagged them as hallucination magnets on gemma4. Defer to a cloud-only v2 if demand appears.

## Proposed architecture

### 1. Zone model (replaces "slot" terminology)

Each skeleton declares zones with `{name, rows, cols, mode}` where `mode ∈ {splice, replace}`:

| Zone            | Mode    | Typical occupants                                      |
|-----------------|---------|--------------------------------------------------------|
| `headwear`      | replace | none, crown, hood_with_ears, antenna, halo, wizard_hat, spikes |
| `facial_hair`   | replace | none, beard_long, beard_short, stubble, mustache       |
| `eye_region`    | replace | two_dots, single_cyclops, oversized_spiral, visor_slit |
| `body_motif`    | splice  | none, screen_dpad, chest_door, belly_stripe, backpack_strap |
| `trailing`      | splice  | none, tail_ringed, tail_curly, hair_drift, cape_flare  |

Five zones, each ≤6 options with `"none"` fronted. LLM expert's ceiling (6 options/slot, 12 total enums) holds.

### 2. Renderer: two-pass composition

1. Start with the base skeleton template.
2. For each zone in the classification JSON, apply its content:
   - `replace`-mode zones rewrite the rows they own (beard occupies rows 6-10, pushing the body signal elsewhere).
   - `splice`-mode zones overlay a single-cell glyph at the zone's anchor in the `accent` or `highlight` color.
3. Apply palette substitution (now including `highlight`).
4. Pad to `CELL_WIDTH`.

Illegal zone/skeleton combos (beard on `ghost_floating`, antenna on `humanoid_tall`) silently coerce to `"none"` via a `_ZONE_COMPAT` table. No retries.

### 3. Two new skeletons (superfan ask)

- `blob_amorphous` — irregular bumpy outline (LSP, talking food with a dominant headwear override)
- `hand_creature` — five-fingered hand silhouette (Hi Five Ghost, Thing, Rayman)

Total: 10 skeletons. Under the LLM expert's gemma4 ceiling of 12. Don't add more.

### 4. JSON schema (final for v1)

```json
{
  "skeleton": "humanoid_tall",
  "palette": {
    "hair": "#...", "skin": "#...", "outfit": "#...",
    "accent": "#...", "shadow": "#...", "highlight": "#..."
  },
  "eye": "●",
  "mouth": "▽",
  "zones": {
    "headwear": "hood_with_ears",
    "facial_hair": "none",
    "eye_region": "two_dots",
    "body_motif": "backpack_strap",
    "trailing": "none"
  }
}
```

Flat top-level, six top-level keys, every new field an enum with `"none"` fronted. LLM expert's constraints satisfied.

### 5. Cloud-path opt-in

When `cfg.cloud_llm.enabled AND secrets.get_cloud_key()`: classifier uses `CloudBackend` with `output_config.format` enforcing the schema. Model: `claude-haiku-4-5`. Fallback to local on any `CloudBackendError`. Wired via a new `_classify_via_cloud` helper next to the existing local path.

## Grounding: how the classifier knows Leela has one eye

The classifier today only gets `profile.source` (franchise name, e.g. "Futurama"). That's why gemma4 falls back to pretraining trivia for specific character tells — and gets them wrong half the time. **No web search needed.** The fix is in the pipe we already run:

1. **Voice training generates a persona card BEFORE the classifier** (same LLM, same run). The card already describes catchphrases, mood list, franchise context.
2. **Extend persona generation with a `visual_tells: string` field** — one sentence of signature shapes: "single huge centered eye, purple ponytail, yellow tank top." Zero added latency; it's one extra field on a call we're already making.
3. **Pipe the full persona card (not just `source`) into `_classify_character_for_skeleton`.** Gemma4 then pattern-matches on text it's already grounded in, not recalling cartoon minutiae from pretraining. "single cyclops eye" in the persona → classifier picks `eye_region: single_cyclops`.
4. Web search remains available as a fallback (observation_enricher pattern, 30d cache, `web_fetches` consent), but voice training runs once per character, so it's overkill for v1.

## Suggested scope — Phase 1 (reordered: grounding first)

**The smoke test forced a reorder.** Without visual grounding, zones and highlight color are polishing the wrong end of the pipeline — Qwen3-14B will keep picking saddle brown for everyone. Ship grounding as milestone 1a, verify it lifts color accuracy, THEN build zones on top.

### Milestone 1a — Grounding (1 day, ship first)

1. **Persona extension**: add a `visual_tells: string` field to the persona-card generation prompt + schema. One sentence listing signature shapes and canonical colors: e.g. "white bear-ear hood, pale skin, cyan tee, navy shorts, green backpack strap." Update `_parse_profile_json` (or equivalent) to preserve it. Backfill prompt explicitly asks for canonical on-screen colors.
2. **Classifier grounding**: `_classify_character_for_skeleton` takes `visual_tells` as a new parameter and splices it into the prompt *above* the persona voice text. Make the visual_tells section load-bearing ("Use these colors and shapes: {visual_tells}") so Qwen3 can't ignore it.
3. **`/voice regenerate` one-shot**: for the 6 voices currently missing ASCII, regenerate personas so `visual_tells` populates, then re-run the classifier. This doubles as the Phase 1a acceptance test.
4. **Pass bar**: rerun the smoke test on Finn / BMO / Bender / Jake / Mordecai. ≥4 of 5 must produce canonical colors (Finn=white+cyan+navy, BMO=mint green, Bender=silver-gray, Jake=golden-yellow, Mordecai=cyan-blue+white+black-mask). If Qwen3-14B still fails ≥2 characters, force cloud-path for classification (Haiku via `CloudBackend`, ~$0.002/voice) and re-test.

### Milestone 1b — Highlight color + headwear zone (1-2 days)

5. Add `highlight` to `PALETTE_KEYS` in `tokenpal/ui/ascii_skeletons.py`. Update existing 8 skeleton templates to reference `{highlight}` on one row each (chest sheen, edge highlight, eye sparkle) per the retro artist's "dark/mid/light" EGA triad.
6. Define `_ZONES` + `_ZONE_COMPAT` tables (new module, e.g. `tokenpal/ui/ascii_zones.py`). Start with `headwear` only — ship one zone end-to-end before widening.
7. Extend every skeleton template with a `{headwear}` region (1-2 rows above the head). Headwear enum: `none, crown, hood_with_ears, antenna, halo, wizard_hat, spikes`. Curated micro-templates for each. `"none"` fronted.
8. Update `tokenpal/tools/train_voice.py`:
   - `_DEFAULT_CLASSIFICATION` gets `"highlight": "#aaaaaa"` + `"zones": {"headwear": "none", ...}`.
   - `_parse_classification_json` gets the compat-table normalization pass.
   - Classifier prompt adds a `## Zones` rubric with 2-3 lines per enum value including franchise negation ("Finn has a hood_with_ears, not a crown").
9. Cloud opt-in: new `_classify_via_cloud` helper gated on `cfg.cloud_llm.enabled`. Use `CloudBackend.output_config.format` with a JSON Schema dict reflecting the contract above. Default `claude-haiku-4-5`.

### Milestone 1c — Testing (parallel with 1b)

10. Golden-hue-bucket assertions for the 5 smoke-test characters (fuzzy on hex, strict on hue band).
11. Legal-combo coverage: every (skeleton, zone) pair resolves to a valid render.
12. Malformed-JSON fallback: stubbed `_ollama_generate` fixtures; no LLM-in-CI.
13. Snapshot `visual_tells` field on regenerated personas so persona drift doesn't silently break grounding.
6. Test suite (`tests/test_voice_ascii_classifier.py`):
   - Golden hue-bucket assertions for Finn, BMO, Ice King, Bender, Hypnotoad.
   - Legal-combo coverage: every (skeleton, zone) pair resolves to a valid render.
   - Malformed-JSON fallback: stubbed `_ollama_generate` fixtures.
   - No LLM-in-CI.

## Phase 2 (follow-up, gated on Phase 1 feedback)

- Remaining four zones (`facial_hair`, `eye_region`, `body_motif`, `trailing`).
- Two new skeletons (`blob_amorphous`, `hand_creature`).
- Mood-aware frame variants: PersonalityEngine's 6 moods each pick a `frame_variant` (grumpy → slit eyes, smug → smirk mouth). Same 3 frames, 18 character-states for free.
- Unified "lit from upper-left" re-shading across all skeletons (the retro artist's art-direction unification — currently every skeleton has its own light direction, making the roster feel like eight art styles).

## Deferred indefinitely

- Texture/pattern slots (hallucination-prone on gemma4, marginal gain).
- Pose variants (multiplies template matrix; low return).
- Braille-pixel density for body regions (font-alignment breaks).
- Per-slot two-tone gradient pairs (confuses gemma4; `highlight` slot achieves same goal).

## Open questions

1. **Zone merge order when two zones write the same row**: e.g. if `facial_hair` extends to row 10 and `body_motif` wants row 10 too. Propose: declare a fixed z-order (`headwear > facial_hair > eye_region > body_motif > trailing`) and document it. Test coverage for the edge.
2. **`hand_creature` skeleton details** — is the "hand" palm-forward (Hi Five Ghost) or side-view (Thing)? Pick one canonical orientation; ship it; don't try to serve both.
3. **Legal/copyright posture** on pre-loaded character-specific enum values (e.g. `hood_with_ears` is pretty obviously Finn, `spiral` is obviously Hypnotoad). Zone values should be **generic shapes** not franchise references; rubric text in the prompt is where franchise context lives. Keeps the templates reusable and litigation-adjacent text out of the codebase.
4. **Regenerate UX** — `/voice regenerate` goes from ~60s to ~75s at `max_tokens=600`. Acceptable? (LLM expert said yes; user to confirm.)

## Test plan for "did this actually ship more character?"

Two tiers of acceptance, both tested against Qwen3-14B-Q4_K_M on the llamacpp path:

**Tier 1 (objective, automated):** rerun the smoke-test roster (Finn / BMO / Bender / Jake / Mordecai). Assert canonical-hue-bucket colors via `_hex_to_hue_bucket`. Must pass ≥4 of 5 after Milestone 1a; must pass 5 of 5 after 1b. Uses existing local personas under `~/.tokenpal/voices/`.

**Tier 2 (subjective, manual — superfan's meta-test):** train 5 voices covering the skeleton roster (Finn, BMO, Ice King, Bender, Hypnotoad), display side-by-side in a 150-cell-wide Textual grid, show to someone familiar with the shows. **Pass bar: they name 4 of 5 unprompted.** Current system (per smoke test) would get 0-1 of 5 because colors are wrong. Milestone 1a alone should get to 2-3 of 5. Full Phase 1 (1a+1b) should clear 4 of 5.

---

**Bottom line**: the 8 existing skeletons are structurally fine. They just don't have enough addressable zones for the classifier to target signature shapes. Add five zones (headwear first), one highlight color, two niche skeletons, and an optional cloud path. Keep everything a closed enum with `"none"` fronted, coerce illegal combos silently, and ship headwear-only end-to-end first so the zone framework is proven before widening.

## Persona analyses

- [Retro graphic artist](ascii-art-retro-artist.md) — silhouette-first, anchor-based accessories, texture enums, EGA triad palette, 16colo.rs techniques
- [LLM expert](ascii-art-llm-expert.md) — enum ceilings, hallucination modes, flat schema, compat table, cloud opt-in, staged rollout ranking
- [Superfan](ascii-art-superfan.md) — recognition checklist for 10 icons, regional-override requirement, accessory priority ranking, two new skeleton asks, meta-test pass bar
