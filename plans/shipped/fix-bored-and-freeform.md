# Fix BORED mood threshold (#13) and freeform dominance (#11)

## Goal
Raise the BORED mood threshold so it doesn't fire after 20s of focused single-app usage, and fix the freeform gate bypass so `_should_freeform()` respects forced silence. Tune freeform constants as belt-and-suspenders.

## Non-goals
- Rewriting the mood system architecture or adding new moods
- Adding new senses or activity signals (typing detection, etc.)
- Changing observation interestingness scoring or topic roulette
- Touching the LLM prompt paths or personality engine

## Files to touch
- `tokenpal/brain/personality.py` lines 503-505 — raise BORED threshold from 10 polls (~20s) to ~90 polls (~3 min)
- `tokenpal/brain/orchestrator.py` lines 33, 53, 278-298 — add forced-silence check to `_should_freeform()`; lower `_FREEFORM_CHANCE_RICH` from 0.30 to 0.20; increase `_FREEFORM_MIN_GAP_S` from 45s to 90s

## Failure modes to anticipate
- Raising BORED threshold too high means mood never shifts during quiet sessions — buddy feels static
- Freeform too rare = dead air during low-activity periods when observations also can't fire
- `_should_freeform()` doesn't check `_forced_silence_until` — freeform fires immediately after forced silence kicks in (root cause of 4:1 ratio)
- Consecutive comment counter increments in `_emit_comment()` (shared by both paths) but forced silence reset only happens in `_should_comment()` — freeform bypasses it entirely
- Over-tuning constants could make buddy feel dead during quiet stretches

## Done criteria
- BORED mood doesn't trigger until ~3 min of same-app usage
- `_should_freeform()` respects `_forced_silence_until`
- `_FREEFORM_CHANCE_RICH` and `_FREEFORM_MIN_GAP_S` tuned down
- Existing tests pass (`pytest`)

## Parking lot

## Parking lot
