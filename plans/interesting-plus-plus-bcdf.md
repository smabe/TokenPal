# interesting++ implementation: B + C + D + F

## Goal
Implement families B (richer predicates), C (observation enrichment), D (multi-tool chains), and F (personalization) from `plans/interesting-plus-plus.md`. Skip A (drop-in new rules using existing tools) and E (new tool actions). Ship in four phased commits, lowest risk first.

## Non-goals
- Family A rules (recipe, air_check, unit_riff, etc.) — deferred.
- Family E new tools (news_headline, weather_alert, etc.) — deferred.
- LLM-initiated tool calling (issue #33 still a non-goal).
- Per-voice rule muting.
- Changing the comment gate, observation gate, or near-dup guard further.
- Tuning the interestingness threshold floor (separate concern if still needed after this).
- Redesigning memory.db schema beyond additive pattern helpers.

## Execution order
1. **Phase D** — chains (smallest surface, immediate payoff)
2. **Phase B** — richer predicates + running-bit promotions
3. **Phase F** — personalization via memory_query patterns
4. **Phase C** — observation enrichment (biggest architectural lift, last)

One commit per phase. Parking lot stays available for "ooh shiny" thoughts across all four.

## Files to touch (per phase)

**Phase D (chains)**
- `tokenpal/brain/idle_rules.py` — add 3 new IdleToolRule entries + predicates
- `config.default.toml` — 3 new commented toggle stubs under `[idle_tools.rules]`
- `docs/idle-tool-rolls.md` — rule catalog table update
- `tests/test_brain/test_idle_rules_predicates.py` — 3 new predicate tests

**Phase B (richer predicates)**
- `tokenpal/brain/idle_rules.py` — promote `long_focus_fact` + `on_this_day_opener` + `lunar_override` to running bits; add `git_shipped_callback` rule + predicate that consumes git sense readings; add streak-aware weight boost on `random_fact` family
- `tokenpal/brain/idle_tools.py` — no changes expected; running-bit machinery already supports opener_framing=""
- `tests/test_brain/test_idle_rules_predicates.py` — predicate tests for git-shipped + streak signals

**Phase F (personalization)**
- `tokenpal/brain/memory.py` — extend `get_pattern_callbacks()` (or add new helpers) with: `get_session_arc_signal()`, `get_daily_habit_window()`, `get_streak_app_runs()`. Sensitive-app filter already in place, inherit it.
- `tokenpal/brain/idle_rules.py` — add `callback_streak`, `session_arc`, `habit_rehearsal` (silent running bit), `anniversary` (easter-egg). Predicates may need new signals plumbed into `IdleToolContext` via `build_context` in `orchestrator.py`.
- `tokenpal/brain/orchestrator.py` — `_build_idle_context` adds the new per-user pattern fields
- `tokenpal/brain/idle_rules.py::IdleToolContext` — extend with the new per-user signals (keep them optional so existing tests don't break)
- `tests/test_brain/test_memory_patterns.py` (new) — pattern-detection tests
- `tests/test_brain/test_idle_rules_predicates.py` — 4 new predicate tests

**Phase C (observation enrichment)**
- `tokenpal/brain/observation_enricher.py` (new) — `ObservationEnricher` class, sense-specific handlers (git, process_heat, new-domain). Same latency posture as `app_enricher`: 3s cap, `memory.db` cache with per-transition TTL, never blocks for cached hits.
- `tokenpal/brain/orchestrator.py` — wire enricher into `_generate_comment`'s snapshot path alongside `_maybe_enrich_snapshot`
- `tokenpal/brain/memory.py` — new cache table via migration (schema v3→v4), one row per (sense, key) with ts + payload
- `tokenpal/senses/app_enricher.py` — left alone (separate from this; same pattern generalized elsewhere)
- `tests/test_brain/test_observation_enricher.py` (new) — cache hit/miss, timeout, sensitive-app skip

## Failure modes to anticipate
- **Running-bit bleed** (Phase B): already at 3-slot cap on `PersonalityEngine`. Promoting three more rules could blow through the cap in a single morning → LRU evictions happen fast → bits never get to ride for their intended duration. Mitigation: pick short `bit_decay_s` for promotions (2h not 8h), and verify in tests that cap stays at 3.
- **Cross-sense predicate brittleness** (Phase B + F): `git_shipped_callback` reads `active_readings["git"].data["last_commit_msg"]`. If git sense's data schema drifts, predicate silently stops firing. Mitigation: sense contract test pinning the `data` keys.
- **Session churn + first_session_of_day drift** (Phase F): `callback_streak` predicate needs stable `session_id`. If the overnight churn recurs, streak detection gets noisy. Already non-bug per last plan, but flag if we see it in new telemetry.
- **Memory.db migration risk** (Phase C): v3→v4 adds an enrichment cache table. Existing migrations are additive; must not rewrite prior rows. Mitigation: standard migration pattern, backup DB before first run.
- **Hot-path latency** (Phase C): every new enricher call is a potential blocking call on the brain tick. `app_enricher` uses a 3s cap + aggressive caching. Any new enricher MUST follow this pattern exactly — no new ones that routinely exceed 3s.
- **Non-deterministic test flakes** (Phase C): network-backed enrichers in tests require mocking. Use the same `@patch` / stub pattern `app_enricher` tests use.
- **Sensitive-app bleed through** (Phase F): memory_query patterns expose app visit histories. Must pass through `SENSITIVE_APPS` filter (already in `get_pattern_callbacks`) — verify new helpers do the same.
- **Framing cringe ceiling** (all phases): each new rule adds surface area for bad framings. Budget real time per rule's framing — a 5-word framing produces reliably bad output. Review framings against Finn voice before committing each phase.
- **Chain tool cascade failures** (Phase D): `friday_wrap`, `coffee_break`, `late_night_host` each call 2-3 tools. Existing `morning_monologue` gracefully degrades on extra-tool failure (continues with what it has); verify the new chains also fail gracefully.

## Done criteria
- Phase D: three chain rules defined + predicates tested + docs updated + 1 commit. Test suite green.
- Phase B: three running-bit promotions + git-shipped + streak awareness shipped + 1 commit. Test suite green.
- Phase F: four personalization rules + new memory pattern helpers + 1 commit. Test suite green.
- Phase C: ObservationEnricher with 3 sense handlers (git, process_heat, new-domain) + 1 commit. Test suite green.
- Overall: can ship any single phase independently without breaking prior phases. Each commit is revertable in isolation.

## Parking lot
(empty at start)
