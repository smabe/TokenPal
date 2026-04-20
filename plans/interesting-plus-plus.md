# interesting++

## Goal
Explore how idle-tool rolls currently work and produce a menu of concrete options for making the buddy's observations more *interesting* — i.e. more varied, more personal, and more surprising — without inflating comment rate or burning the user's attention. Deliverable for the morning: this plan file (options + tradeoffs), not code.

## Non-goals
- Implementing any option in this pass — user reads in the morning and picks.
- LLM-initiated tool calling during idle (issue #33, explicitly deferred in docs/idle-tool-rolls.md).
- Adding any network dependency that isn't keyless + free tier.
- Changing the gate, pacing, or 8-per-5min cap — the roller stays silence-only.
- Per-voice rule muting (known non-goal in the doc).
- Touching the observation or freeform paths — idle-roll is the only emission path in scope.

## Files to touch
None yet — this is research + options. Eventual implementation would touch some subset of:
- `tokenpal/brain/idle_rules.py` — add `IdleToolRule` entries, new predicates
- `tokenpal/brain/idle_tools.py` — roller (only if we extend the data model)
- `tokenpal/brain/orchestrator.py` — `_build_idle_context` if new signals get plumbed in
- `tokenpal/actions/**` — new tool actions if an option requires them
- `config.default.toml` — `[idle_tools.rules]` stubs
- `docs/idle-tool-rolls.md` — rule catalog table updates
- `tests/test_brain/test_idle_rules_predicates.py` — predicate unit tests

## Overnight evidence (2026-04-19 20:00 → 2026-04-20 07:31)

Pulled from `~/.tokenpal/memory.db`:
- **Idle-tool fires in 11.5h: 1.** `deep_lull_trivia` at 20:25:59 — `emitted=0` (swallowed by `filter_response`). Zero rolls during the 23:07-01:48 and 01:48-07:31 dead-air windows where the roller should have thrived.
- **Chat lines in that window: 182** (`chat_log`, speaker=Finn). Observation path dominated; gate almost never chose silence until the user was fully asleep, and by then the LLM had collapsed into stock phrases like "kinda cool, my bud" (5+ near-duplicates visible in screenshot).
- **`session_start` rows overnight: 68.** Session context keeps re-initializing — `session_minutes` never grows, `first_session_of_day` is probably stuck True. This silently kills `_settled_in_session`, `morning_word`, `on_this_day_opener`, `morning_monologue`, `monday_joke` (Monday AM), `memory_recall`. **Likely the largest single cause of the no-fire result.** Correlates with the 20:00-21:20 model/server thrash (gemma2/3/4 + localhost/apollyon swaps) in chat_log.
- **LLM-initiated tool hallucination**: 21:27:52 Finn said "Let me check what's up with the word of the day!" — no actual roll fired. The model narrated a tool call it never made. Textbook #33 (why LLM-initiated idle tool-calling is deferred).
- **Sensor snapshot**: `app_awareness` fired 358 `app_switch` rows + `idle` fired 14 `idle_return` rows. Senses are alive; the dead path is idle-tool rolls.

**Diagnosis**: `interesting++` cannot start from adding rules. The existing catalog doesn't get a fair shot because (a) session churn poisons predicate context, (b) the observation gate starves the roller, and (c) `filter_response` can still swallow a successful fire with no retry. Fix these before expanding.

## Current state (what's shipped)

**Emission path.** Three brain outputs: observation, freeform, idle-roll. Idle-roll runs only when the gate chose silence, so it fills dead air without bumping the rate cap.

**Rule catalog — 11 rules in `M1_RULES`:**
- `evening_moon`, `lunar_override` — moon_phase
- `morning_word` (running bit, 8h) — word_of_the_day
- `monday_joke`, `todays_joke_bit` (silent running bit, 4h) — joke_of_the_day
- `weather_change`, `morning_monologue` (chain: +sunrise_sunset +on_this_day) — weather_forecast_week
- `long_focus_fact` — random_fact
- `deep_lull_trivia` — trivia_question
- `on_this_day_opener` — on_this_day
- `memory_recall` (offline floor) — memory_query

**Tools registered but NOT used by any idle rule** (opportunity surface):
- Network: `air_quality`, `book_suggestion`, `crypto_price`, `currency`, `random_recipe`, `sports_score`
- Utilities: `convert`, `timezone`
- Focus: `logs`, `pomodoro`, `reminders`
- Introspection: `git_log`, `grep_codebase`, `list_processes`, `read_file`, `system_info`

**Context inputs to predicates** (`IdleToolContext`): now, session_minutes, first_session_of_day, active_readings, mood, weather_summary, time_since_last_comment_s, consent_web_fetches. Hour + weekday derived.

**Running bits**: 3-slot LRU on `PersonalityEngine`, soft `{output}`-templated system-prompt injection over `bit_decay_s` seconds. Only `morning_word` (opener+bit) and `todays_joke_bit` (silent bit) use them today.

**Chains**: `morning_monologue` is the only multi-tool rule — forecast + sunrise + on-this-day bundled into one riff.

**Warm cache**: 5 evergreens pre-fetched at session start (6h TTL). Everything else hits the network live.

## Option menu

**Start with family 0 — the fires aren't happening today, so new rules won't help.** Then pick 1-2 from A + B, and optionally something ambitious from C or E.

### 0. Pre-requisite fixes — make the existing roller actually fire
None of the options below move the needle until these land. Probably ~1 day of work, not exploratory.

- **Session-start churn investigation**: 68 `session_start` rows overnight. Find the write site (likely `orchestrator.py` session bookkeeping or a model-switch code path that rebuilds `Brain`), and gate so a transient reconnect doesn't emit a new session. Add a debug log when `first_session_of_day` resets. This single fix probably un-breaks 5 predicates.
- **Gate starvation**: the observation gate chose "comment" ~1/min for hours of low-signal time, crowding out `maybe_fire`. Two options:
  - *Tighten*: bump the near-duplicate guard — if the last 3 comments share a 4-gram (e.g. "kinda cool, my bud"), force silence next tick. Measurable via `chat_log` scan.
  - *Quota*: guarantee at least N idle-roll attempts per hour when time_since_last_comment > X, by having the gate route to `maybe_fire` before the comment branch when the activity envelope is flat.
- **Swallow telemetry**: today when `filter_response` nukes an idle riff, we record `emitted=0` but don't retry, don't escalate, don't log why. Add the reason + either retry once with tightened framing, or count toward a daily "swallow budget" that shifts the rule's weight down automatically. Gives the framing-tuning feedback loop real signal.
- **Freeform fallback pool audit**: the 18:00-20:00 and mid-night "amnesia filler" lines ("Is this a dream or a do-over?", "Why do I smell like pineapples?") look like a tiny hard-coded pool that was burning in place of real output. Locate the path, decide whether it's the `easter_eggs` set, a freeform default, or an actual LLM degenerate loop. If it's a fixed pool, shrink its role; if it's degenerate LLM output, gate it behind `is_clean_english`.

Six families, roughly ordered from lowest effort / most obviously additive to biggest architectural lift. They're not mutually exclusive — a good morning pick is probably 1-2 from family A + B + something ambitious from C or E.

### A. Drop-in new rules using existing unused tools
Lowest-friction wins. Each is a new `IdleToolRule` + predicate + framing. No new actions.

- **`weekend_recipe`** — `random_recipe` on Sat/Sun lunch-window, first settled lull. Running bit (6h) so the buddy can callback "still thinking about the galette" later.
- **`air_check`** — `air_quality` when outdoor weather is noteworthy (smoke, storms, or heat) OR zip configured + first morning session. Paired well with `weather_change`. Respect the zip-is-optional posture — skip if no location.
- **`match_day`** — `sports_score` if `memory_query` shows the user mentioned a team name recently, OR only fires when a future `/team <name>` command is introduced. Low priority until we have a user signal — sports with zero context is cringe.
- **`book_rec`** — `book_suggestion` once a week, settled afternoon lull, weight low. Good running-bit candidate (48h decay, callback "you start Piranesi yet?").
- **`market_check`** — `crypto_price` only if user has `/ticker BTC` style config; otherwise skip. Same gate as `match_day`. Weight low; easily cringe.
- **`fx_check`** — `currency` travel-mode: fires only when `[weather].zip` country-of-origin != saved home-country. Cute but narrow.
- **`unit_riff`** — `convert` after a sense reading mentions a number (e.g. hardware sense says "24GB RAM") and rolls a converted-to-something-silly line ("that's ~4,800 PS2 memory cards"). Needs a cheap regex scan of active_reading summaries — new predicate signal.
- **`timezone_call`** — `timezone` late-night lull, references a city where it's currently morning. Cute, low effort.

### B. Richer predicates — use signals we already have but ignore
Same tool catalog, sharper "when this fires" logic.

- **Running-bit conversions**: `long_focus_fact`, `on_this_day_opener`, and `lunar_override` are all currently one-shot. Converting a couple to running bits (with short decay, 2-3h) would let the LLM weave them into later observations instead of each fact being orphaned. The single biggest lever for "stickier" commentary per unit of effort.
- **Weather + mood cross**: a gloomy mood + rainy weather_summary predicate could unlock a different framing for `random_fact` ("in the spirit of the weather, here's a damp fact"). Pure framing change, zero new tools.
- **Streak-aware rules**: `productivity` sense already tracks streaks. A "you're on a hot streak — don't jinx it" predicate could trigger `random_fact` with a framing that acknowledges the streak. Needs plumbing productivity readings into `IdleToolContext` explicitly (easy — `active_readings` already carries them, just a predicate helper).
- **Git-signal rules**: `git` sense emits `last_commit_ts` + `last_commit_msg`. A rule that fires when HEAD just changed and is *not* a WIP (the git_nudge handles WIP) — "you actually shipped something, let me celebrate with a fact / trivia". Good running-bit candidate.
- **Typing-cadence + tool**: sustained `furious` typing in a code app → idle rule fires a `memory_query` callback ("you coded this fast last Tuesday — crashed later that night") on the *post-burst silence* rather than blasting a fact mid-flow.

### C. Reactive enrichment — pull tool output into *observations*, not just idle rolls
This is the ambitious one. Today, idle-rolls are a third path. But the buddy already has `app_enricher.py` which blocks the first observation tick for a new app on a `search("<app> software")` call and splices the result in. Generalize that idea.

- **Observation enrichers**: lightweight "auto-look-up" slots on select sense transitions. Examples:
  - Git commit landed → `git_log` → "commit 4 in the last hour, all under 20 lines — you're on a rebase."
  - New domain in browser title → lookup service describes it → framing "first time on X today".
  - New process spikes in `process_heat` → `list_processes` explains it ("that's Docker's daemon; you started compose 40s ago").
- **Pro**: observations become *situational intel*, not just "you switched to Cronometer".
- **Con**: every enrichment is a potential latency spike on the hot path. `app_enricher` already caps at 3s and caches in `memory.db`; same posture would apply. Costs a lot of framing discipline — enrichments that read like trivia dumps would be worse than silence.
- **Scope**: this is a different shape than idle rolls. Could ship as a parallel system (`ObservationEnricher`) or bolt additional hooks into the existing enricher.

### D. Chain expansion — more `morning_monologue`-style multi-tool riffs
Chains are currently one (`morning_monologue`). They're disproportionately high-signal because the riff has more to weave.

- **`friday_wrap`** — joke + random_fact + on_this_day (Fri afternoon).
- **`coffee_break`** — word_of_the_day + trivia_question (mid-morning second session, settled).
- **`late_night_host`** — trivia_question + random_fact + moon_phase (after 23:00, first lull).
- Each costs ~1-2 extra HTTP calls but they're mostly evergreens (warm cache). Low implementation cost once the chain infra is proven.

### E. New tool actions (real engineering)
Options requiring actual new `AbstractAction` implementations. Higher effort, bigger payoff.

- **`news_headline`** — a single headline from a keyless source (HN Algolia already in use; could be generalized). Different from `world_awareness` sense: the sense fires on-change; an idle rule would fetch on-demand + let the buddy riff.
- **`transit_status`** — optional, e.g. BART/MTA status if user provides a line. Almost certainly overkill for this buddy.
- **`weather_alert`** — severe weather lookup (Open-Meteo supports alerts). Fires on predicate "there IS an alert for your zip". Would be genuinely useful, not just flavor.
- **`local_events`** — city-level event scrape. Every free API here is sketchy. Probably skip.
- **`space_weather`** — NOAA space weather API (keyless). Northern-lights callouts on a predicate. Delightful; narrow.
- **`github_status`** — is GitHub up? Fires in deep-focus + coding app + user hit save 3x in 10s. Surprisingly useful executive-function signal.
- **`shower_thought`** — a *category* rule: pick from HN "Ask HN" or a random-quote endpoint, idle-lull trigger.

### F. Personalization layer — memory_query-powered rules
The offline-floor rule (`memory_recall`) has the right shape but a blunt implementation. It picks a random metric and riffs on it.

- **`callback_streak`** — detect "user opened this app N days in a row at roughly this time" from memory.db, fire a callback. Needs a pattern-detection helper (`MemoryStore.get_pattern_callbacks()` already has skeleton code — extend it).
- **`anniversary_rule`** — first run of TokenPal N weeks ago today → bit. Easter-egg flavor.
- **`habit_rehearsal`** — user opens [journal app] at 10pm M-F → silent running bit at 21:45 "journal time soon" without nagging.
- **`session_arc`** — detect "this session is much longer / shorter than typical" from history. Fire a single observation about the arc rather than another sense riff.
- These are higher effort (pattern detection is finicky), highest reward (truly personal).

## Failure modes to anticipate
- **Cringe ceiling**: more rules raise the odds of a voice-mismatched riff. Framing strings are the load-bearing detail — a new rule with a 5-word framing will reliably produce bad output. Budget real time per rule's framing, not just the predicate.
- **Near-duplicate spam**: adding 8 more rules means the gate's near-duplicate guard matters more. If two rules produce similar outputs back-to-back, it'll feel repetitive even if each was fine in isolation.
- **Predicate drift**: predicates that depend on sense readings (e.g. "user just committed") are brittle — sense contracts change. Each new cross-sense predicate needs a sense-level regression test, not just a predicate test.
- **Latency in option C**: observation enrichment ≠ idle rolls — it sits on the hot path. Any network call there must be bounded + cached, per the existing `app_enricher` pattern. Violating this is a regression in overall perceived latency.
- **Consent + privacy**: several E-family options would add new network endpoints. Each must gate under `web_fetches` consent AND pass through `contains_sensitive_term`. Easy to forget when dropping in "one more fun API".
- **Warm cache explosion**: adding 10+ evergreens to `_DAILY_EVERGREEN_TOOLS` means session start makes 10+ HTTP calls before the buddy speaks. Keep the warm set tight; non-evergreens stay live-fetched.
- **Model cap**: small quantized models don't weave more than 2-3 injected "running bits" well. 3-slot LRU is load-bearing; expanding it would degrade riff quality.
- **Running-bit bleed**: "ride-along" framing across many bits causes the model to force-mention all of them in every line. Already a risk at 2 bits; worse at 3+. Any family-B running-bit promotions need a bake-off round where we watch for forced mentions.

## Done criteria
- This plan file exists with overnight diagnostic evidence + option families 0-F enumerated and their tradeoffs laid out (done on write).
- User reads it in the morning and picks a subset to work on (critical path: family 0 items first, then any of A-F).
- A parking-lot entry or separate plan exists for each family NOT picked, so ideas don't get lost.

## Parking lot suggestions for the morning
- Family 0 fixes are non-negotiable before A-F, but they can absolutely ship as one commit each and don't need a new plan each.
- If only one thing is picked, it should be the **session-start churn** fix — highest leverage, smallest surface.
- The `filter_response` swallow telemetry is a prerequisite for any rule-framing tuning work; without it you're flying blind.

## Parking lot
(empty at start — append "ooh shiny" thoughts that surface mid-work for later)
