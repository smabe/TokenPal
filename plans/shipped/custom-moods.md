# Custom Moods Per Voice

## Goal
Voice training generates character-specific mood names and descriptions instead of using the hardcoded 6 TokenPal moods. BMO gets PLAYFUL/BLAH/TURBO, not SNARKY/BORED/HYPER. Default TokenPal keeps its existing 6 moods unchanged.

## Design Decisions (from brainstorm)
- **Keep Mood enum internally** — `update_mood()` is untested, don't rewrite it. Add display-name resolution layer on top.
- **One LLM call, pipe-delimited** — `DEFAULT | NAME | description` format. Roles pre-filled, fill-in-the-blank style. One retry, legacy fallback.
- **Role-keyed storage** — `mood_prompts` keys are roles ("default", "sleepy") for new profiles. Old profiles keep "snarky" etc. keys and fall through.
- **2 AM guardrail universal** — late-night override unchanged (enum comparison stays).

## Non-goals
- Dynamic mood transitions (LLM-generated transition rules) — parked V2, see GitHub #6
- Changing the default TokenPal mood set (hardcoded 6 stay as-is)
- Custom mood counts — always generate exactly 6 moods so the mapping stays 1:1
- UI changes beyond what's already shown (mood name in status bar already works)
- Fine-tuning pipeline changes — this is voice training only
- Editing mood names post-training (retrain is fast enough for v1)

## Files to touch
- `tokenpal/tools/voice_profile.py` — add `mood_roles: dict[str, str]` and `default_mood: str` fields, update `load_profile()` and `make_profile()`
- `tokenpal/tools/train_voice.py` — rewrite `_generate_mood_prompts()` with pipe-delimited prompt, regex parser, retry/fallback. Return mood_roles + default_mood alongside mood_prompts
- `tokenpal/brain/personality.py` — add `_mood_names: dict[str, str]` in `_apply_voice()`, add `_ENUM_TO_ROLE` mapping, update `_mood_line()` to 3-tier fallback (role-keyed voice prompt → legacy key → hardcoded), update `mood` property to return custom display name. **`update_mood()` unchanged.**
- `tests/test_brain/test_voice.py` — tests for custom mood display, mood_line with custom prompts, hot-swap
- `tests/test_brain/test_mood.py` (new) — tests for `update_mood()` heuristic triggers (currently zero coverage), both default and custom mood paths

## Failure modes to anticipate
- LLM generates fewer or more than 6 moods — validation rejects, 1 retry, then legacy fallback
- LLM generates mood names that don't parse (multi-word, punctuation) — regex rejects non-matching lines
- LLM generates duplicate mood names for different roles — uniqueness check rejects
- `_mood_line()` crash if custom mood string not in enum-keyed `_MOOD_PROMPTS` — solved by 3-tier fallback (role lookup first)
- Existing voice profiles on disk lack `mood_roles`/`default_mood` — `.get()` with empty defaults, falls through to legacy path
- `mood` property must return custom display name for status bar — resolved via `_mood_names` lookup

## Done criteria
- `_generate_mood_prompts()` uses pipe-delimited prompt, parses with regex, returns mood_roles + default_mood
- `VoiceProfile` has `mood_roles` and `default_mood` fields, backward-compatible
- `PersonalityEngine` displays custom mood names via `_mood_names` lookup
- `_mood_line()` has 3-tier fallback: role-keyed → legacy key → hardcoded
- `update_mood()` is unchanged but now has test coverage (new test file)
- Status bar and `/mood` command show custom mood name when voice active
- Existing voice profiles without mood_roles fall back gracefully
- Tests pass: `pytest tests/test_brain/`

## Build order
1. VoiceProfile: add fields (smallest change, backward compat)
2. PersonalityEngine: _mood_names lookup, mood property, _mood_line()
3. Training prompt: pipe-delimited format + parsing + fallback
4. Tests: P0 + P1 (~14 tests)

## Parking lot
- V2: LLM-generated mood transition rules — see `docs/dynamic-mood-transitions.md` and GitHub issue #6
- V2: Edit mood names post-training without retraining
