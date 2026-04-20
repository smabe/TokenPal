# research-refinement

## Goal
When `/refine` detects that the cached sources can't answer the follow-up, fire a small supplemental search (1-2 queries), merge new sources into the pool, and re-synth — so a topic pivot like "what about the vic 20?" actually surfaces real info instead of a polite refusal.

## Non-goals
- Not replacing `/research` — `/refine` stays a lightweight follow-up path; supplemental search is bounded (≤2 queries, one re-synth attempt, no deep-mode escalation).
- Not changing the `/research` pipeline itself (planner, fetch, validate stay as-is).
- Not adding a new slash command — all wiring happens inside the existing `/refine` flow.
- Not adding a dedicated "confidence score" sense or surfacing confidence numerics to the user — a single boolean `needs_fresh_search` signal is enough.
- Not removing the /cloud deep | /cloud search refuse path — those still lack cached excerpts, so supplemental refine still doesn't apply there.

## Files to touch
- tokenpal/brain/research.py — add a refine-specific `SYNTH_SCHEMA_REFINE` variant (the shared `SYNTH_SCHEMA` stays untouched — initial synth + deep mode contracts don't change) with `needs_fresh_search: bool` + `gap_query: str`, update `_REFINE_PROMPT` to request those fields, rewrite `Research.refine()`: first cloud pass → if `needs_fresh_search` → run up to `refine_max_supplemental` supplemental searches via `_search_many` + existing fetch path, dedup new hits via `_canonical_url()` against cached pool → second cloud pass. One round max, no loop.
- tokenpal/brain/orchestrator.py — extend `_format_research_summary()` to accept an optional `supplemental_info: str | None` arg (cleaner than shoehorning into the hardcoded `counts` list). Refine handler passes it when supplemental fires. Also writes expanded sources back to `research_cache` (capped by new config knob).
- tokenpal/brain/memory.py — add `MemoryStore.append_research_sources(question_hash, new_sources, cap)` — read-modify-write under existing `self._lock`, truncates to cap to keep row size bounded.
- tokenpal/config/schema.py — add `CloudSearchConfig.refine_max_supplemental: int = 2` and `CloudSearchConfig.refine_cache_max_sources: int = 15` (growth cap for the appended pool). Default-on behavior: supplemental fires when flag is set; cap prevents unbounded growth.
- tokenpal/ui/cloud_modal.py (or wherever the cloud settings modal lives — verify exact path during impl) — surface `refine_max_supplemental` as an integer input (label: "Refine: max extra searches"). Persist via the existing modal write path.
- tokenpal/app.py — minor: extend the "Refining: ..." status to note "(may fetch more sources)" so users know it can go wider than cached pool.
- tests/test_research.py (extend existing) — cover: pool-sufficient path (no supplemental), pool-insufficient path (supplemental fires, dedup works), supplemental-fails path (return honest gap answer), cap behavior (cached pool truncates after appends). Reuse `_FakeCloud` + `_hit()` helpers.

## Failure modes to anticipate
- Cloud model over-triggers `needs_fresh_search` (says "need more" when cached pool actually covers it) → wasted search+fetch calls. Mitigate by prompt-constraining when to set the flag (only when no cited source supports the follow-up's key noun phrases).
- Cloud model under-triggers (cached pool really is inadequate but model guesses from parametric memory) → we're back to the current failure. Prompt must say "don't invent picks from training memory" (already there) AND encourage setting the flag in the same edge case.
- Supplemental search returns URLs already in the cached pool → dedup via `_canonical_url()` (research.py:145, the same normalizer that strips tracking params in the thin-pool top-up).
- Supplemental fetch fails (paywall, bot wall, all results dead) → fall back to the factual "sources don't cover this" answer, include a note that we tried N supplemental queries.
- Second cloud synth still flips `needs_fresh_search=true` → do NOT loop; return whatever answer it managed and log warning. One supplemental round, that's it.
- Backend routing for supplemental search: respect `/cloud tavily` when active, otherwise DDG. Brave routing is parked (see parking lot) — users with Brave active will get DDG supplemental for now.
- `/cloud deep` or `/cloud search` is on: current `_handle_refine()` excerpt gate (orchestrator.py:2007-2021) already refuses. No change there.
- Cost visibility: each supplemental round = 1 extra cloud synth call (~$0.024 on Haiku default) + up to `refine_max_supplemental` search calls + 1-N fetch calls. Must log telemetry line so users can see when a refine went wide vs. stayed cached.
- Cache growth: appended sources capped by `refine_cache_max_sources` (default 15) inside `MemoryStore.append_research_sources()`. Oldest sources evicted first so recent supplemental additions stay.
- Race: write-back serialized via the existing `MemoryStore._lock` (the helper does read-modify-write under that lock). No new orchestrator-level guard needed — concurrent refines are rare (user-driven, single queue).
- Telemetry dual-sink: the existing `_agent_log` teeing pattern (app.py:244) extends to the refine-supplemental path so users see "= done in Xs (2 quer(ies), 3 new source(s), supplemental)" in chat AND session log.
- Schema contract bleed: `SYNTH_SCHEMA_REFINE` must NOT replace `SYNTH_SCHEMA` in any call site other than `Research.refine()`. Regression risk: initial synth + deep mode accidentally get the refine schema and start emitting `needs_fresh_search` fields the orchestrator doesn't read.

## Done criteria
- `/refine "what about the vic 20?"` on a C64 research session fires ≤2 supplemental searches, finds VIC-20 CPU info (MOS 6502), and returns a cited answer citing the new source(s).
- `/refine "what clock speed did the 6510 run at?"` on the same session stays in the cached pool (no supplemental fires) because the answer is already there. Verified by log output.
- New sources added by supplemental refine are visible to a follow-on `/refine` (cache write-back works, pool doesn't reset to the original five).
- After N supplemental rounds on one question, the cached pool stays ≤ `refine_cache_max_sources` (default 15). Verified by test.
- `refine_max_supplemental` is adjustable from the cloud settings modal; default 2. Setting it to 0 effectively disables supplemental refine (fallback to today's re-synth-only behavior).
- Unit tests cover pool-sufficient, pool-insufficient-success, pool-insufficient-fetch-fails, and cache-cap paths.
- Status line in chat log shows supplemental telemetry (queries fired, new sources count) when the path triggers.
- Existing `/refine` users on `/cloud deep` or `/cloud search` still hit the current refuse path unchanged.
- mypy + ruff clean on touched files.

## Parking lot
- Brave backend routing for `/research` + supplemental refine. `_resolve_backend()` (research.py:455) currently only knows Tavily + DDG; Brave key wiring exists for display but no routing. Users with `/cloud brave` active get DDG supplemental for now. Remind at ship time — this is a recurring "I added a backend but didn't route it" gap.
