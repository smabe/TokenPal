# Voice-ASCII Classifier — LLM/Prompt Engineering Review

Reviewer lens: what gemma4 (default) and Qwen3-14B can reliably emit as JSON, and where the current schema in `tokenpal/tools/train_voice.py:_classify_character_for_skeleton` can grow without regressing.

## 1. Enum size ceiling

Current: 8 skeletons + 7 eye glyphs + 6 mouth glyphs. Gemma4 handles 8-way enums fine when each option has a **distinctive word-level label** (`humanoid_tall`, `robot_boxy`, `ghost_floating`) and a one-line description. The failure mode isn't "confused between 8 and 9", it's picking the **first option** on ambiguous inputs — you'll see `humanoid_tall` bias if the description is fronted.

Safe zone: **up to ~12 skeletons** if every new one has a clearly disjoint silhouette (e.g. `insectoid`, `blob_amorphous`, `mech_bipedal`). Beyond 12, gemma4 starts clustering on label tokens ("humanoid_tall" vs "humanoid_thin" vs "humanoid_slim" become interchangeable). Qwen3-14B holds up to maybe 20, but we ship gemma4 as default, so **design for 12**.

For accessory/pattern sub-enums, keep each one **≤6 options** with one being an explicit `"none"`. Small enums degrade gracefully; long ones (>10) inside a nested slot tank accuracy because the model now has three ambiguity axes stacked.

## 2. JSON schema shape

Tiered recommendation for resilience on gemma4:

- **Flat top-level keys, not nested objects beyond one level.** The current single `palette` nesting works. Adding a second nested group (`accessories.hat`, `accessories.glasses`) roughly doubles bracket-mismatch failures in my experience with small models. Prefer flat `hat`, `glasses`, `antenna` at the top level.
- **Every new slot is an enum string with an explicit `"none"` option.** Not a boolean. `"hat": "crown" | "helmet" | "hood" | "cap" | "none"`. Booleans invite the model to say `true` then omit the style sub-field.
- **Optional keys are a trap.** Gemma4 drops them silently when uncertain; the parser then has to infer "missing = none" vs "missing = malformed". Require every field and force `"none"`.
- **No free-form strings.** Every field is either an enum or a validated hex. Anything else (e.g. "describe the hat in one word") hallucinates. The existing `_HEX_COLOR_RE` gate in `_parse_classification_json` is the right pattern and should be copied for any new field.

## 3. Grounding and few-shot blow-up

The current prompt (lines 380–405) is ~50 lines of enumerated template descriptions with no few-shot examples — just a franchise hint. Adding 3–4 accessory slots with 5 options each adds roughly 30–40 lines of enum docs. That's fine.

But: **do not add multi-character few-shot examples**. Each example would need to include the full target JSON, which bloats the prompt by ~150 tokens/example and primes the model to echo the example character's skeleton. This is the same failure mode freeform art hit — gemma4 latches onto the exemplar.

Instead, add a **short per-slot "when to pick" rubric** under each new field, 2–3 lines each. This is what the existing skeleton list does (`humanoid_tall: standard hero/adventurer (Finn, Mordecai)`). That pattern scales.

## 4. Hallucination modes

Gemma4's dominant failure on "pick hat style" for a hat-less character: it **picks the most generic option** ("cap") rather than `"none"`. Even with `"none"` listed.

Mitigations that work:
- Put `"none"` **first** in the enum list, not last. Recency bias kicks for the last item; fronting `"none"` combined with explicit instruction ("If the character has no canonical X, pick 'none'") moves the prior.
- Add a **franchise-negation example** in the per-slot rubric: `accent_pattern: "none" (Finn has no pattern — use 'none', don't invent stripes)`.
- Temperature 0.5 → 0.3 retry (current pattern) helps here too: lower temp makes `"none"` more likely when it's a reasonable choice.

Expect a 10–20% "invented accessory" rate on gemma4 even with these. That's why **validation + legal-combo checks** matter (next section).

## 5. Validation layer

Illegal combos (hat on `ghost_floating`, wings on `animal_quadruped`) should **silently normalize, not retry**. The retry cost is another 2–4s per voice on a local LLM; not worth it when the fix is deterministic.

Proposed flow in `_parse_classification_json`:
1. Parse + hex-validate as today.
2. Apply a per-skeleton **compat table**: `_SKELETON_COMPAT = {"ghost_floating": {"hat", "glasses"}, "robot_boxy": {"antenna"}, ...}`. Any slot not in the set gets forced to `"none"`.
3. Log the coercion at DEBUG so `/voice ascii` regressions are traceable.

Retries should be reserved for **malformed JSON**, not semantic issues. The current two-temp retry is already the right call for parse failures.

## 6. Token budget

Current output JSON: ~100 tokens. Current prompt: ~350 tokens. `max_tokens=400`.

Each new slot adds ~10–15 output tokens (field name + enum value + quotes + commas). Three accessory slots + one pattern slot + `"none"` defaults is ~50–60 extra output tokens. **Bump `max_tokens` to 600**; anything under 800 is still a single-shot generation on gemma4 at ~30 tok/s (~20s total, acceptable).

Prompt growth is the bigger risk. Current 350 tokens → adding rubrics for 4 new fields → ~550 tokens. Still well under gemma4's 4k effective window. But note: `_ollama_generate` calls with `disable_reasoning=true`. If someone flips that on during debugging, ~900 extra reasoning tokens appear and `max_tokens=600` truncates the JSON mid-emit. Add a test assert that reasoning is disabled in the classifier path specifically.

## 7. Prompt caching + regenerate UX

Classifier is called once per voice, so per-call caching doesn't help. `/voice regenerate` currently runs 5 parallel generators + ASCII art serially (~60s total per docs). The ASCII call is ~5s of that.

A 2x fatter ASCII prompt at max_tokens=600 is maybe 8–10s. Still within budget. `/voice regenerate --all` across say 5 voices goes from ~300s to ~320s. Acceptable. Don't optimize for caching here.

## 8. Cloud synth opt-in

**Recommend yes, gated on the same `cfg.cloud_llm.enabled` flag as `/research`.** Rationale:

- Voice training runs once. Cost: Claude Haiku 4.5 on a 500-token prompt + 300-token JSON output is ~$0.002 per voice. Sonnet is ~$0.01. Rounding errors vs `/research`.
- `CloudBackend` in `tokenpal/llm/cloud_backend.py` already has `output_config.format` schema enforcement (line 167, 306). Wire the classifier JSON Schema through it and you **eliminate the parse-failure retry entirely** on the cloud path — the SDK enforces the shape.
- Bigger win: cloud models pick `"none"` correctly, don't invent accessories, and handle 15+ skeleton enums without drift. The hallucination-mode discussion above is a gemma4 constraint, not a cloud constraint.

Wiring:
- Add a `_classify_via_cloud` helper next to `_classify_character_for_skeleton` in `train_voice.py`.
- Gate on `cfg.cloud_llm.enabled AND secrets.get_cloud_key()` (mirror `research_action._build_cloud_backend`).
- Fall back to the local path on any `CloudBackendError`.
- Default model `claude-haiku-4-5`, not Sonnet — this is classification, not synthesis.

Silent local fallback keeps the existing UX for users without a key.

## 9. Regression testing

Snapshot-test the **classification output**, not the rendered frame. Rendered frames are deterministic given classification; test `_render_skeleton_frames` separately (unit-level).

Proposed test suite:
- **Golden-palette assertions**: `{ "Finn": skeleton="humanoid_tall", hair_hex in {"#ffffff"}, outfit_hex startswith "#3" or "#4"}`. Fuzzy on exact hex, strict on hue bucket (blue-ish = hue 200–240). Helper: `_hex_to_hue_bucket(hex) -> "blue" | "red" | ...` and assert the bucket, not the literal.
- **Legal-combo table**: for each skeleton, assert illegal accessory slots normalize to `"none"`.
- **Franchise negatives**: test that Jake (Adventure Time) never gets a `banned_names` accessory label like "Bender's eyepiece".
- **No-API CI**: stub `_ollama_generate` with recorded-good and recorded-bad fixtures. One happy path per skeleton (8 tests), a handful of malformed-JSON fixtures to assert fallback kicks.
- **Cloud path**: if wired in, stub `CloudBackend.generate` with schema-valid fixtures and assert no retries.

No LLM-in-the-loop tests in CI — flaky and expensive. Keep those as an optional `pytest -m llm_integration` path.

## 10. Staged rollout — ranked by recognition gain per JSON-complexity unit

Ranked by best payoff-to-risk:

1. **Extended palette: 6th `highlight` color slot.** One extra hex field, no new enum, zero combo concerns. Gives color-layered characters (Prismo's rainbow, Lumpy Space Princess's two-tone) a way to read distinctly. Trivial to validate, trivial to ignore in existing skeletons (just don't reference `{highlight}` in templates that don't use it). **Do this first.**

2. **Accessory layering: single `headgear` slot with enum `{"none", "crown", "hat", "hood", "helmet", "halo", "antenna"}`.** Biggest recognition win per slot (Ice King's crown, Angel's halo, BMO's antenna are all iconic). One slot means one anchor point per skeleton, one compat table, bounded complexity. Requires per-skeleton template updates — each skeleton needs a `{headgear}` slot at its crown anchor, rendered as `""` when `"none"`.

3. **Per-skeleton body variants: `build` enum `{"default", "thin", "wide"}` scoped per skeleton.** Moderate win (Muscle Man vs Finn), but requires 2-3x the template work. Defer until accessory proves out.

4. **4th reaction frame.** Easy to add (one more mouth glyph variant in `_TALKING_MOUTH`-style lookup), but low recognition gain — users barely notice idle_alt today. Low priority.

5. **Texture/pattern slots.** Highest complexity, lowest return on gemma4. Hallucination-prone ("does Finn have stripes?" → the model confabulates). **Skip on local, enable only on cloud path if at all.**

6. **Pose variants.** Multiplies the template matrix by N poses × 8 skeletons = unmaintainable. **Skip.**

### Recommended Phase 1 (2-3 days of work)
- 6th palette slot `highlight`
- `headgear` accessory enum with `"none"` fronted
- Compat table in `_parse_classification_json`
- Cloud-path opt-in via `CloudBackend.output_config.format`
- Golden-palette + legal-combo test suite

That delivers ~70% of the recognition gain the brainstorm list hints at, for ~20% of the complexity of implementing the full menu.

## Specific code-path recommendations

- `tokenpal/tools/train_voice.py:327` `_DEFAULT_CLASSIFICATION` — extend with `"highlight": "#aaaaaa"` and `"headgear": "none"` so the fallback matches the new schema.
- `tokenpal/tools/train_voice.py:415` `_parse_classification_json` — add the compat-table normalization pass before returning. Keep the retry loop scoped to parse-level failures only.
- `tokenpal/ui/ascii_skeletons.py:214` `PALETTE_KEYS` — add `"highlight"`; `render()` already fails loudly on missing keys so this forces template-update completeness.
- `tokenpal/ui/ascii_skeletons.py:200` `SKELETONS` — add a `{headgear}` format slot at the crown anchor of each skeleton with a matching no-op default when headgear is `"none"`.
- `tokenpal/llm/cloud_backend.py` — no changes needed; existing `output_config.format` surface is sufficient. Classifier just needs a JSON Schema dict.
- New test file `tests/test_voice_ascii_classifier.py` — golden-palette + legal-combo + malformed-JSON fallback coverage.

## Blunt bottom line

Gemma4 is a classifier, not a designer. Keep every new field an enum with an explicit `"none"`, keep nesting shallow, validate + coerce server-side, and use the cloud path when a user has a key. The one extension that gives the most recognition gain per unit of JSON-schema risk is a single `headgear` accessory slot plus a 6th highlight color — everything else is diminishing returns on a small local model.
