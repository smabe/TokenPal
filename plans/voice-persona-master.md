# Voice Persona Redesign

## Goal
Fix issue #12 — character voices drift to generic speech. Replace the one-sentence persona with a structured voice card, add catchphrase priming at the generation point, add anchor-line sampling, and add cross-franchise guardrails.

## Non-goals
- TF-IDF scoring (needs scikit-learn dep)
- Voice drift metrics in memory.db
- A/B comparison tooling
- Training-time validation (generate test responses before saving)
- Character-specific sentence caps
- Full CharacterFingerprint dataclass with quality scoring
- Changing the VoiceProfile JSON schema beyond adding `anchor_lines` and `banned_names`

## Files to touch
- `tokenpal/tools/train_voice.py` — new `_generate_persona` prompt (structured card, 25 samples, 500 max_tokens), `_score_lines()` heuristic, `_extract_anchor_lines()`, `_derive_banned_names()`
- `tokenpal/tools/voice_profile.py` — add `anchor_lines: list[str]` and `banned_names: list[str]` fields to dataclass
- `tokenpal/brain/personality.py` — parse catchphrases from persona at load, new `_voice_reminder` with catchphrase priming, tiered `_sample_examples`, banned_names filter in `filter_response`
- `tokenpal/app.py` — add `/voice regenerate` subcommand to `_handle_voice_command`

## Failure modes to anticipate
- Ollama structured output doesn't parse — need regex fallback to use raw text
- NEVER section Pink Elephant problem — small models may produce forbidden words MORE. Monitor after deployment.
- Catchphrase parsing fails on malformed persona string — need graceful fallback to current meta-reminder
- anchor_lines empty for profiles with very few distinctive lines — fallback to full pool
- `/voice regenerate` on old profile with no lines — guard against empty lines list
- Banned names too aggressive — common English words that happen to be character names (e.g. "Pops") could false-positive
- gemma4 may ignore structured persona if it's too long — 150-180 token budget keeps it tight

## Done criteria
- [ ] `_generate_persona()` produces structured VOICE/CATCHPHRASES/NEVER/WORLDVIEW card
- [ ] Voice reminder at generation point shows catchphrase examples, not meta-instruction
- [ ] Few-shot sampling draws 60% from anchor pool when available
- [ ] `filter_response()` suppresses cross-franchise name mentions
- [ ] `/voice regenerate` command works on existing profiles
- [ ] All 8 existing profiles regenerated with new persona format
- [ ] 274 existing tests still pass
- [ ] Manual spot-check: run Bender + Finn for 5 min each, verify character fidelity in logs

## Parking lot
