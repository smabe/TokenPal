# be-brave

## Goal
Finish wiring the Brave search backend so `/research` (and the new supplemental `/refine` path) actually routes queries to Brave when `/cloud brave` is active, instead of silently downgrading to DuckDuckGo. Closes the "I added a backend but didn't route it" gap surfaced during the research-refinement plan's research pass.

## Non-goals
- Not reworking the planner prompt — it already lists Brave (research.py:1508-1509); no changes needed there.
- Not touching the Brave HTTP client, auth header, or JSON parsing — those ship in `senses/web_search/brave.py` and `senses/web_search/client.py:237-286` already. Tests in `tests/test_web_search.py:180-299` cover them.
- Not reworking Tavily routing. Tavily still wins over Brave when both are active — preloaded content means fewer fetches downstream.
- Not replacing DDG as the "no keys configured" fallback.
- Not adding a Brave-specific config section or modal UI. Presence-of-key = active; cloud modal already surfaces the key input.

## Files to touch
- tokenpal/brain/research.py — `_default_backend()` (lines 451-453) picks the default when a planner query has no explicit backend AND when `/refine` fires supplemental search. Today it only knows Tavily-vs-DDG. Add Brave to the precedence chain: Tavily (when cloud_search_active) > Brave (when key present) > DDG. This one change unblocks both `/research` default routing AND refine supplemental search — refine inherits from `_default_backend()` without additional changes.
- tests/test_research.py — one new test: `_resolve_backend` + `_default_backend` with a Brave key in `api_keys` routes to Brave instead of downgrading. Mirror the existing Tavily routing test pattern.

## Failure modes to anticipate
- Precedence bug: if we flip the order and put Brave before Tavily, users with both keys get silently downgraded from preloaded-content Tavily to snippet-only Brave. Rule: Tavily > Brave > DDG. Write the test with BOTH keys set to pin this.
- Refine supplemental inherits from `_default_backend()` so the fix reaches that path automatically — but the research-refinement test suite doesn't currently assert backend choice in the supplemental path. Add a spot-check (or rely on the routing test being enough).
- Key rotation: `api_keys` dict is built once per ResearchRunner construction via `load_search_keys()`. User re-runs `/cloud brave <new-key>` mid-session; next `/research` or `/refine` call must pick up the new key. Already works today (every research call builds a fresh runner), but worth confirming nothing caches the api_keys at module level.
- Silent downgrade logging: today `_resolve_backend` logs at INFO when it downgrades an explicit planner choice. `_default_backend` currently doesn't log anything because it's never a "downgrade" — it's the base default. After this change, picking Brave (rather than DDG) is still a default decision, not a downgrade, so no new log line is warranted — but note this in a comment so future-me doesn't add noise.

## Done criteria
- `_default_backend()` returns `"brave"` when Brave key is set AND Tavily isn't active; returns `"tavily"` when Tavily is active (regardless of Brave key); returns `"duckduckgo"` when neither is keyed.
- `/refine` supplemental search picks Brave when `/cloud brave` is on and Tavily is off (verified by the test — supplemental calls `_default_backend()` so this is automatic).
- Unit test covers: tavily-wins-when-both-active, brave-wins-when-only-brave-keyed, ddg-when-neither-keyed.
- mypy + ruff clean on `tokenpal/brain/research.py`.
- Full `pytest` suite green.

## Parking lot
(empty at start)
