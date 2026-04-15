# Voice generation hardening

## Why
Both `finn 1.json` and `finn 2.json` shipped with gemma4 drift: greetings in German,
offline_quips in Thai, mood_prompts/roles/default_mood empty, structure_hints empty,
one persona in Chinese meta-commentary, one ASCII frame set all empty strings.
Every failure was silent — filters only gated on string length, and on parse failure
generators fell through to `[]`/`{}` with no warning. `/voice regenerate` currently
only refreshes persona + ASCII, so it cannot fix the other four broken fields.

## Done criteria
- [ ] `/voice regenerate <name>` refreshes **all** LLM-generated fields (greetings,
      offline_quips, mood_prompts/roles/default_mood, structure_hints, persona, ASCII)
- [ ] Any generator output that is non-English, contains meta-commentary, or fails
      structural validation is rejected and retried (max 3 tries, temperature ramp down)
- [ ] Training and regeneration print a post-run health report; any field still empty
      or rejected shows up as a WARN line
- [ ] `python -m tokenpal.tools.train_voice --audit [slug|--all]` prints a per-profile
      health report and exits non-zero if anything is broken
- [ ] After `/voice regenerate finn`, the two Finn files have clean greetings, quips,
      moods, hints, persona, and ASCII (verified by `--audit`)
- [ ] Tests for validators + retry + full regenerate

## Plan
1. `_is_clean_english(text)` — reject >10% non-ASCII, reject meta tokens
   (`Wikipedia`, `copiert`, `Analyze`, `user's request`, `I cannot`, `If the goal`,
   leading/trailing `**`, bare `**…:**`)
2. `_generate_with_retry(fn, validate, attempts=3)` — temp ramps 0.9 → 0.7 → 0.5
3. Apply to:
   - `_generate_lines_from_prompt` (greetings, offline_quips, structure_hints)
   - `_generate_persona` (must contain `VOICE:` AND `CATCHPHRASES:`)
   - `_generate_mood_prompts` (already retries; add English-gate; drop silent `{}`
     fallback — warn instead)
   - `_generate_ascii_art` (reject if any frame has <4 non-empty lines)
4. Append `"Write only in English. Plain text. No markdown, no analysis, no meta."`
   to every prompt that currently lacks it.
5. Rename `regenerate_persona` → `regenerate_voice_assets`; regenerate everything
   except `lines`, `banned_names`, `finetuned_*`, `created`. Keep
   `regenerate_persona` as thin alias (call with `persona_only=True` flag) so
   existing callers compile.
6. Add `--audit` CLI branch to `tokenpal/tools/train_voice.py`.
7. Tests in `tests/test_tools/test_train_voice_hardening.py` covering validator
   edge cases, retry behavior, audit output.

## Out of scope
- Re-scraping transcripts; `lines` stays intact
- Retraining LoRA / finetune metadata
- Fixing gemma4 drift at its root (we just guard our side)

## Rollout
After merge, user runs `/voice regenerate finn` once to restore the profile in place.
