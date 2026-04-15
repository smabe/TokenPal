# TokenPal Grand Improvement Plan: Agents, Research, Tools

## Context
Current tool registry has 4 actions (`timer`, `system_info`, `open_app`, `do_math`). The registry already supports `safe` and `requires_confirm` flags designed for autonomous tool-calling, but nothing wires it up yet. `/ask` exists as a one-shot web search but has no planner, no synthesis, and no citations.

This plan expands TokenPal from a commentary-buddy into a commentary-buddy-plus-assistant. Two audiences: power users (devs) and normal users (everyone else). Both share the same agent/research infrastructure.

---

## Goal

Ship a tool registry expansion, an agent loop, and a Claude-style research command, in that order. Each phase is shippable on its own.

---

## Phase 0: Tool Picker + Consent UI (foundation for everything below)

Must land before phase 1 ships any new opt-in tool. Makes enable/disable pleasant instead of typing `/senses enable <name>` eight times.

- Default tools (`timer`, `system_info`, `open_app`, `do_math`) stay on by default, no opt-in needed
- All new tools from phases 1-5 are opt-in behind this UI
- New `tokenpal/ui/tool_picker_modal.py` built on Textual `SelectionList`
  - `ModalScreen[dict | None]` so `dismiss()` and the `push_screen(modal, callback)` pattern are correctly typed
  - **Grouping is NOT native** in `SelectionList` (runtime rejects `Separator`/plain `Option`). Use stacked `SelectionList` widgets, one per section, each preceded by a `Label` header inside a `VerticalScroll`
  - Sections: `Default` (read-only, shows for transparency), `Local` (phase 1), `Utilities` (phase 2), `Focus` (phase 3), `Agent` (phase 4), `Research` (phase 5)
  - Pre-populate checked state via `Selection(prompt, value, initial_state=True)`
  - Don't add a screen-level `space` binding (it shadows the built-in toggle). Call `.focus()` on the first list in `on_mount` so the modal lands on checkboxes, not the Save button
  - On save: read `.selected` from each SelectionList, `dismiss({section: [values]})`. The `push_screen` callback writes to disk via a new `tokenpal/config/tools_writer.py` that upserts `[tools] enabled_tools = [...]` in config.toml (same pattern as `senses_writer.py`). Keep the modal itself pure (no I/O).
- `/tools` opens the modal. Keep `/tools list` as plain-text fallback for headless or console overlay
- Reuse the same modal for `/senses`: factor into `SelectionModal(title, groups, on_save)` so the sense picker gets the upgrade for free
- Consent UI piggybacks on this: a separate `SelectionModal` invocation with category rows (`web fetches`, `location lookups`, `external keyed APIs`, `research mode`). One-time consent, stored at `~/.tokenpal/.consent.json` with per-category booleans. No per-session re-prompt

**Exit criteria:** `/tools` opens, user checks 4 new tools, saves, restarts, all 4 are available. `/senses` uses the same modal.

---

## Phase 1: Local Context Tools (power user, no network)

Low risk, high delight, works with `gemma4` today. Ship first because it proves the registry at scale.

- `read_file(path)` — git-tracked files only, size cap 200KB, reject paths matching `.env|credentials|secrets|*.key`
- `grep_codebase(pattern, path?)` — ripgrep wrapper, cap 100 matches, respects `.gitignore`
- `git_log(since?, author?, limit=20)` — wraps `git log --oneline`
- `git_diff(ref?)` — defaults to working tree vs HEAD
- `git_status()` — porcelain output
- `list_processes(top_n=10)` — psutil, CPU/memory sorted
- `memory_query(metric)` — reads own `memory.db`: time-in-app, switches/hour, streaks (metrics whitelisted)

**Config:** opt-in via the phase 0 tool picker. Default tools stay the current 4 (`timer`, `system_info`, `open_app`, `do_math`). All phase 1 tools land disabled, user checks what they want.

**Exit criteria:** user can ask `/ask "what did I do yesterday"` and buddy uses `git_log` + `memory_query` to answer in character.

---

## Phase 2: Normal-User Utility Tools (reactive, mostly network)

The pitch-to-non-devs batch. Organized by effort within the phase.

### 2a. Zero-API (built-in math, no network)
- `convert(value, from_unit, to_unit)` — units via `pint`, bundled dataset
- `timezone(city)` — `zoneinfo`, city-to-tz lookup table (top 200 cities)
- `sunrise_sunset(location?)` — pure astronomy math from existing lat/lon
- `moon_phase(date?)` — pure math

### 2b. Free keyless APIs (verified April 2026)
- `currency(amount, from, to)` — **open.er-api.com/v6/latest** or **frankfurter.app** (exchangerate.host went keyed in late 2023, do not use)
- `weather_forecast_week(location?)` — extend existing Open-Meteo call
- `pollen_count(location?)` / `air_quality(location?)` — Open-Meteo air-quality endpoint (10k req/day non-commercial)
- `random_fact()` — uselessfacts.jsph.pl
- `joke_of_the_day()` — icanhazdadjoke.com (must send `User-Agent` + `Accept: application/json`)
- `word_of_the_day()` — Wordnik RSS (main JSON API still needs free key)
- `on_this_day()` — Wikipedia `/api/rest_v1/feed/onthisday/events/MM/DD` (descriptive User-Agent required)
- `random_recipe(ingredient?)` — TheMealDB with test key `1`. Only random + search endpoints free; latest-meal is Patreon-gated, skip those
- `trivia_question(category?)` — opentdb.com (use session token to avoid duplicate-question errors; informal ~1 req/5s cap)
- `sports_score(team)` — TheSportsDB test key `3` (livescore is Patreon-gated, basic scores free)
- `crypto_price(symbol)` — CoinGecko public endpoint throttled to ~5-15 req/min. If we hit 429s often, switch to the free "Demo API" key (30 req/min with signup)
- `book_suggestion(genre)` — Google Books keyless (1k req/day per IP; add free API key for 100k/day if we outgrow)

### 2c. Free-tier keyed APIs (env var, opt-in)
- `what_to_watch(mood?, genre?)` — TMDB (free signup, reliable, ~50 req/s soft cap)
- `stock_price(ticker)` — **Stooq CSV** (keyless) or **Finnhub** (60 req/min free). Alpha Vantage cut free tier to 25 req/day, unusable
- `flight_status(flight_number)` — **OpenSky Network** registered free tier, NOT AviationStack (AviationStack free is 100 req/mo HTTP-only, useless)
- `book_suggestion(genre)` with API key — upgrade path from 2b if volume warrants

### 2c-deferred
- `package_track` — dropping. 17track free tier is 100 trackings **total** as trial, not sustainable. Revisit if users provide their own paid key
- Alpha Vantage, AviationStack free tiers, exchangerate.host keyed version — all rejected per verification

**Config:** opt-in via the phase 0 tool picker. Keyed APIs additionally gated by env var presence (no key = checkbox shows as disabled with a tooltip).

**Privacy:** all external text wrapped in `<tool_result>` delimiters, filtered through `contains_sensitive_term` before prompt composition. Same pattern as `/ask`. Network-touching tools (2b, 2c) require the `web fetches` consent checkbox from phase 0.

---

## Phase 3: Time/Focus Tools (hybrid reactive/proactive)

This is where "buddy character" pays off over generic utilities.

- `pomodoro(work_min=25, break_min=5)` — wraps `timer`, announces phases in character
- `stretch_reminder(interval_min=60)` — opt-in proactive nudge
- `water_reminder(interval_min=90)` — same
- `eye_break()` — 20-20-20 rule, opt-in proactive
- `bedtime_wind_down(target_time)` — at T-60 buddy starts suggesting wrap-up
- `hydration_log()` / `habit_streak(name)` / `mood_check()` — user-initiated, stored local only

**Config:** opt-in via the phase 0 tool picker. Proactive reminders (`stretch`, `water`, `eye_break`, `bedtime_wind_down`) appear as separate checkboxes so users can enable pomodoro without also signing up for water reminders.

**Design rule:** proactive nudges surface as speech bubbles (not OS notifications), pause during active conversation, and pause during sensitive-app detection. All logs go to `memory.db` at 0o600.

---

## Phase 4: Agent Mode (`/agent <goal>`)

New brain path. Thin loop over the registry.

### Architecture
- New `tokenpal/brain/agent.py` with `run_agent(goal, max_steps=8)` function
- Loop: LLM call with `tools=registry.as_openai_tools()` → execute tool calls → feed results back → repeat until no tool calls or step cap
- **Gate confirmation in the executor, not the loop**: before dispatching a `requires_confirm=True` action, the executor `await`s a `confirmation_future` that the modal resolves. Loop state stays in memory, no checkpointer needed
- Hard caps: step cap 8, per-run token budget 12k (track via `usage.total_tokens` from non-streaming Ollama responses), per-step timeout 45s
- **Arg parsing**: Ollama returns `tool_calls[].function.arguments` as a JSON **string**, not a dict. Always `json.loads()` with try/except; small models occasionally emit malformed JSON
- **Tool call IDs**: generate fallback IDs (`f"call_{i}"`) if the model emits empty `tool_call_id` strings, or Ollama silently drops tool-result messages
- Sensitive-app detection kills the session mid-run
- Tool calls log to chat log as `→ tool(args)` / `← result` for transparency
- Final synthesis shows in speech bubble
- Fail loudly on cap hit: return partial trace so the brain can comment in-character about the stall

### UI
- `/agent <goal>` slash command routes to agent path, not conversation path
- Observations/freeform suppressed during active agent run
- Textual modal for confirm-gated tools (reuses existing modal patterns)

### Model
- `[agent] model = "qwen2.5:32b"` config override, falls back to main `[llm] model_name`
- **Do NOT use `deepseek-r1:32b` for agent execution.** R1 wraps tool calls in `<think>` tags and frequently emits tool JSON inside reasoning rather than the `tool_calls` field (same failure mode as Qwen3, already documented in CLAUDE.md). R1 is viable for research *planning* only, not loop execution.
- Ranking for tool-call reliability: `qwen2.5:32b` (BFCL ≈ GPT-4o-mini) > `llama3.3:70b` (most robust, 40GB+ VRAM) > `gemma4` (fine-tune regression risk if training lacked tool traces) > `gemma4:26b`

**Exit criteria:** `/agent summarize my day` chains `git_log` + `memory_query` + `list_processes` and produces a single in-character summary.

---

## Phase 5: Research Mode (`/research <question>`)

Claude-style plan → search → read → synthesize.

### Pipeline
1. **Planner** (LLM call 1, temperature ≤ 0.3): decompose question into 1-5 search queries. Emit 1 for single-factual lookups; 3-5 only for distinct sub-topics. Output JSON list of `{query, intent}`. Include 2-3 few-shot examples (single-hop, multi-hop, comparative) to prevent over-decomposition
2. **Search** (parallel, no LLM): `asyncio.gather(..., return_exceptions=True)` across existing DDG IA / Wikipedia / Brave backends. Filter exceptions instead of letting one failure kill the gather. Wrap each backend in `asyncio.wait_for(..., 5.0)` — Wikipedia hangs on edge pages
3. **Reader** (LLM call 2, conditional): for each promising hit, fetch full page via `fetch_url`
4. **Synthesizer** (LLM call 3): sources as numbered list `[1] <url>\n<excerpt>`, placed *before* the question in the user turn (recency bias favors citation fidelity). Cap marker range in the prompt. Tail-anchor the citation instruction (repeat at end of user message) to survive long context. Post-hoc regex validation strips dangling `[N]` markers

### Rate-limit gotchas per backend
- **DDG HTML endpoint**: 429s at ~20 req/min per IP. IA endpoint (`api.duckduckgo.com`) is laxer
- **Wikipedia REST**: generous (200 req/s) but requires a descriptive `User-Agent` header or 403
- **Brave Search API**: 1 req/sec free tier, strict. **Per-backend semaphore, not global**
- Share a single `aiohttp` session across all backends for connection pooling

### New actions
- `search_web(query)` — exposes existing `/ask` backends as a tool
- `fetch_url(url)` — `trafilatura` for clean extraction (primary), `readability-lxml` fallback. 500KB size cap, sensitive-term filter. Neither executes JS. `newspaper3k` is abandoned — do not use; if we ever need its style, `newspaper4k` fork is the live continuation
- `list_tabs()` — optional, macOS-only via Quartz window list

### Prior art to clone prompts from
**Farfalle** (`rashadphz/farfalle`) — Python Perplexity clone, planner + synthesizer prompts in `backend/prompts.py`. Closest fit to our stack. Secondary: **Perplexica** (`ItzCrazyKns/Perplexica`) for LangChain-portable prose.

### Config
- Opt-in via phase 0 tool picker (`research` checkbox) plus the `research mode` consent row
- `max_queries = 3`, `max_fetches = 5`, `token_budget = 6000`
- Consent is one-time, stored in `~/.tokenpal/.consent.json` alongside other categories. No per-session re-prompt.

**Model:** split roles. Planner + synthesizer can use `deepseek-r1:32b` (reasoning helps query decomposition and synthesis, and neither emits tool calls so R1's tag-wrapping isn't a problem). Reader stays on `qwen2.5:32b` or similar since it doesn't benefit from reasoning. Main observation path stays on `gemma4`.

---

## Phase 6: Polish + Scale

Only after phases 1-5 are shipped.

- Tool discovery UX: `/tools [list|describe <name>|enable <name>]` slash command
- Tool usage stats in `memory.db` so buddy can riff on "you ask me to convert units a lot"
- Agent mode caching: repeated identical tool calls within a run return cached result
- Research result caching: same question within 24h returns cached synthesis with a "cached" marker
- Per-tool `rate_limit` field in action metadata, enforced by registry

---

## What We're Not Doing

- Email/calendar/messaging content reads: drawn-line boundary, stays drawn
- Browser history queries: creepy even with consent
- Location history: weather uses configured zipcode, not tracking
- Photo/screen-content OCR: out of scope, privacy quagmire
- "AI life coach" mode: buddy is a witty observer, not an instructor
- Voice/STT for agent invocation: deferred until phase 6+

---

## Model Requirements by Phase

| Phase | Min viable model | Recommended | Why |
|---|---|---|---|
| 1 (local tools) | `gemma4` | `gemma4` | 7 tools, simple routing |
| 2 (normal tools) | `gemma4` | `gemma4` | reactive only, one tool per call |
| 3 (focus tools) | `gemma4` | `gemma4` | mostly non-LLM ritual wrappers |
| 4 (agent mode) | `gemma4:26b` | `qwen2.5:32b` | multi-step planning degrades below 26B |
| 5 (research) | `qwen2.5:32b` | `deepseek-r1:32b` | planner + synthesizer benefit from reasoning |

Registry size thresholds (from BFCL / ToolBench evals through mid-2025):
- Gemma 2 9B class (`gemma4`): >90% routing accuracy at 8 tools, ~70% at 16, collapses past 24
- `qwen2.5:7b`: reliable to ~12 tools
- `qwen2.5:14b` / `qwen2.5:32b`: reliable to 20-30
- `llama3.3:70b`: 40+ tools without major degradation

Phase 2 ships 15+ tools total. That's past the gemma4 cliff. **Mitigation: category-based subsetting.** The brain picks a category first (`local`, `normal`, `focus`) based on user intent, then only passes that subset to the LLM. This matches how `/senses` is already siloed.

---

## Implementation Order

| Phase | Effort | Impact | Ship gate |
|---|---|---|---|
| 0: Tool picker + consent UI | ~4h | Foundational | `/tools` modal saves to config, `/senses` reuses it, consent.json round-trips |
| 1: Local context tools | ~6h | High (power users) | All 7 actions have tests, grep cap enforced |
| 2a: Zero-API utilities | ~4h | High (normal users) | 4 actions, unit tests |
| 2b: Keyless APIs | ~8h | Medium-high | Network mocks, sensitive-term filter verified |
| 2c: Keyed APIs | ~6h | Medium | Env-var gating, no key = graceful disable |
| 3: Focus tools | ~6h | High (stickiness) | Proactive pause-during-conversation works |
| 4: Agent mode | ~10h | High (unlocks phase 5) | Step cap, token budget, confirm modal |
| 5: Research mode | ~12h | High (flagship) | Citations, consent gate, reasoning-model config |
| 6: Polish | ~8h | Medium | Tool stats, caching, rate limits |

**Total:** ~64 hours of work, 9 shippable increments.

---

## Decisions Made

1. **Default tools stay default**, all new tools opt-in through a Textual `SelectionList` picker (phase 0). Shared modal serves `/tools`, `/senses`, and the consent dialog.
2. **Consent is one-time**, stored in `~/.tokenpal/.consent.json` with per-category booleans. Never re-prompt per session.
3. **Proactive reminders use speech bubbles**, not OS notifications. On-brand, and the existing conversation-pause / sensitive-app-pause logic applies for free.
4. **Agent mode eats the Ollama swap latency**. Don't run two models concurrently for now. Revisit if the swap cost is painful in practice.

## Open Questions

1. **Tool subsetting trigger**: confirmed we need it before phase 2 ships (15+ tools past gemma4's 8-tool cliff). Open sub-question: use a cheap LLM call to pick the category, or keyword-heuristic? Keyword is faster but fragile.
2. **Agent mode public**: ship `/agent` as a debug command first, or polish UI before user-facing?
3. **Package tracking**: dropped from phase 2 due to 17track's 100-lifetime-tracking free tier. Worth circling back with a user-provides-API-key pattern, or skip permanently?
4. **Flight/stock APIs**: OpenSky + Stooq are the replacements for AviationStack + Alpha Vantage. Both work but have quirks (OpenSky needs signup, Stooq returns CSV). Worth including in phase 2c or defer?

---

## Dependencies on Existing Work

- Registry (`@register_action`) already supports the `safe` / `requires_confirm` flags this plan needs.
- `ConversationSession` pattern extends naturally to `AgentSession`.
- `/ask` consent gate + sensitive-term filter is the prior art for phases 2c and 5.
- Memory.db at 0o600 is already the privacy-safe storage pattern phase 3 reuses.
- Textual modal patterns exist; agent confirm modal is additive.

No breaking changes required to existing senses, brain, or UI.

---

## Research Log

Plan refined April 2026 based on four parallel research passes:

1. **Textual `SelectionList`**: native widget rejects separators/disabled options inside a single list. Use stacked lists with `Label` headers inside a `VerticalScroll`. `ModalScreen[dict | None]` + `push_screen(..., callback)` is the correct pattern.
2. **Ollama tool calling**: `tool_choice` unsupported; args come as JSON strings; `tool_call_id` fallbacks required; `deepseek-r1:32b` unusable for tool execution (emits calls inside `<think>` tags). `qwen2.5:32b` is the sweet spot for agent loops. Registry cliff at 8 tools for gemma-2 class.
3. **Research pipeline**: planner needs temperature cap + few-shot; synthesizer needs tail-anchored citation instruction + regex validation; `asyncio.gather(..., return_exceptions=True)` + per-backend semaphores. Farfalle is closest prior art to our stack. `trafilatura` + `readability-lxml` fallback for extraction.
4. **API endpoint verification**: `exchangerate.host` now keyed (swap to `open.er-api.com` or `frankfurter.app`). `17track` free tier is unusable. `AviationStack` free tier is unusable (OpenSky replacement). `Alpha Vantage` cut to 25 req/day (Stooq/Finnhub replacement). Most other endpoints (Wikipedia, Open-Meteo, icanhazdadjoke, opentdb, TMDB, TheSportsDB, Google Books, CoinGecko) confirmed working with header/throttle caveats.
