# ASCII Art — Character Recognition (active)

Full design: `plans/brainstorm/ascii-art-master.md`.

## Goal
Voice buddies become individually recognizable. Smoke-test baseline: 0-1 of 5 characters have canonical colors. Target: 5 of 5 after Phase 1.

## Test model
Qwen3-14B-Q4_K_M on llamacpp. Smoke test on `~/.tokenpal/voices/{finn,bmo,bender,jake,mordecai}.json`.

## Phase 1a — Grounding (1 day)
1. Add `visual_tells: str` field to persona-card generation + schema.
2. Pipe `visual_tells` into `_classify_character_for_skeleton` above the persona voice text.
3. Regenerate ASCII for the 5 smoke-test voices.
4. Pass bar: ≥4 of 5 canonical hue buckets. If fail, force cloud path.
5. `/simplify` + commit.

## Phase 1b — Highlight color + headwear zone (1-2 days)
1. Add `highlight` to `PALETTE_KEYS`. Update 8 skeleton templates to reference `{highlight}`.
2. New module `tokenpal/ui/ascii_zones.py` — zone definitions + `_ZONE_COMPAT` table.
3. Add `{headwear}` format slot to all 8 skeletons. Enum: `none, crown, hood_with_ears, antenna, halo, wizard_hat, spikes` (none fronted).
4. Update `_DEFAULT_CLASSIFICATION` + `_parse_classification_json` (compat-table normalization).
5. Classifier prompt: add Zones rubric with franchise negation.
6. Cloud opt-in: `_classify_via_cloud` gated on `cfg.cloud_llm.enabled`. Haiku default.
7. Smoke test passes 5 of 5.
8. `/simplify` + commit.

## Phase 1c — Tests (parallel with 1b)
1. Golden hue-bucket assertions for smoke-test 5.
2. Legal-combo coverage for every (skeleton, zone) pair.
3. Malformed-JSON fallback fixtures. No LLM-in-CI.
4. `/simplify` + commit.

## Out of scope
- Phase 2 (remaining 4 zones, 2 new skeletons, mood-aware frames).
- Texture slots, pose variants — deferred indefinitely.
