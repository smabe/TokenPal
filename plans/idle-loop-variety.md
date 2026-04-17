# Idle Loop Variety

**Problem:** Buddy gets stuck repeating near-identical productivity observations during idle periods (see 10× "64 switches per hour / hyperactive squirrel / what happened next?!" burst in user screenshot 2026-04-17).

## Root causes
1. `productivity` sense emits confidence=1.0 every poll because `switches_per_hour` is a session accumulator that drifts by ~1 between polls → summary string mutates → treated as "changed" → 1.5× change_bonus in topic roulette → productivity wins the weight lottery structurally.
2. Prompt-cache + low temp + fixed few-shot anchors + 98%-identical snapshot → Qwen locks onto one template and cannot climb out.
3. Orchestrator has no "I just said something that rhymed with this" guard.

## Fix 1 — bucket-gate productivity
`tokenpal/senses/productivity/memory_stats.py`
- Replace live integers in `_build_summary` with bucket labels (`restless pace`, `active multitasking`, `deep focus`, etc.). Integers stay in `stats` dict for LLM context but leave the summary string.
- Track `(time_bucket, switch_bucket, streak_bucket)` tuple; set confidence=1.0 only when the tuple differs from previous poll's tuple. Confidence=0.0 otherwise — mirrors the `typing_cadence` hysteresis pattern.

## Fix 2 — near-duplicate output guard
`tokenpal/brain/orchestrator.py`
- Keep `deque[str]` of last 5 emitted observation/freeform lines.
- Before `_emit_comment`, compute char-trigram Jaccard against each recent line.
- If max similarity ≥0.70, drop the line and log `Gate: near-duplicate output suppressed`.
- Observation/freeform only. Conversation replies bypass (legitimate repetition possible).

## Test plan
- Unit test bucket transition logic in productivity sense.
- Unit test trigram-Jaccard helper (exact dup = 1.0, unrelated = <0.3).
- Manual: run with current config, watch chat log for 20 min, confirm productivity doesn't monopolize and no back-to-back twins.

## Out of scope (follow-up)
Idle tool-calling (joke_of_the_day etc. firing autonomously). Separate plan — `plans/idle-tool-rolls.md` if user greenlights.
