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
  - Groups: `Default` (read-only, shows for transparency), `Local` (phase 1), `Utilities` (phase 2), `Focus` (phase 3), `Agent` (phase 4), `Research` (phase 5)
  - Checkbox per tool, label = tool name + one-line description
  - On save: delegates to a new `tokenpal/config/tools_writer.py` that upserts `[tools] enabled_tools = [...]` in config.toml (same pattern as `senses_writer.py`)
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

### 2b. Free keyless APIs
- `currency(amount, from, to)` — exchangerate.host
- `weather_forecast_week(location?)` — extend existing Open-Meteo call
- `pollen_count(location?)` / `air_quality(location?)` — Open-Meteo air-quality endpoint
- `random_fact()` — uselessfacts.jsph.pl
- `joke_of_the_day()` — icanhazdadjoke.com
- `word_of_the_day()` — Wordnik RSS
- `on_this_day()` — Wikipedia `/onthisday/`
- `random_recipe(ingredient?)` — TheMealDB
- `trivia_question(category?)` — opentdb.com

### 2c. Free-tier keyed APIs (env var, opt-in)
- `package_track(carrier, tracking_number)` — 17track free tier
- `flight_status(flight_number)` — AviationStack free tier (100/month)
- `sports_score(team)` — TheSportsDB
- `stock_price(ticker)` / `crypto_price(symbol)` — Yahoo Finance / CoinGecko
- `what_to_watch(mood?, genre?)` — TMDB
- `book_suggestion(genre)` — Google Books

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
- New `tokenpal/brain/agent.py` with `run_agent(goal, max_steps=6)` function
- Loop: LLM call with `tools=registry.as_openai_tools()` → execute tool calls → feed results back → repeat until no tool calls or step cap
- Per-run token budget (default 8000), hard step cap (default 6)
- `requires_confirm=True` actions prompt via Textual modal before firing
- Sensitive-app detection kills the session mid-run
- Tool calls log to chat log as `→ tool(args)` / `← result` for transparency
- Final synthesis shows in speech bubble

### UI
- `/agent <goal>` slash command routes to agent path, not conversation path
- Observations/freeform suppressed during active agent run
- Textual modal for confirm-gated tools (reuses existing modal patterns)

### Model
- `[agent] model = "qwen2.5:32b"` config override, falls back to main `[llm] model_name`
- Reasoning models (`deepseek-r1:32b`) worth it here but optional

**Exit criteria:** `/agent summarize my day` chains `git_log` + `memory_query` + `list_processes` and produces a single in-character summary.

---

## Phase 5: Research Mode (`/research <question>`)

Claude-style plan → search → read → synthesize.

### Pipeline
1. **Planner** (LLM call 1): decompose question into 3 search queries
2. **Search** (parallel, no LLM): `asyncio.gather` across existing DDG/Wikipedia/Brave backends
3. **Reader** (LLM call 2, conditional): for each promising hit, fetch full page and extract clean text
4. **Synthesizer** (LLM call 3): all results + citations → answer with `[1] [2]` footnotes

### New actions
- `search_web(query)` — exposes existing `/ask` backends as a tool
- `fetch_url(url)` — new action: `trafilatura` for clean extraction, 500KB size cap, sensitive-term filter
- `list_tabs()` — optional, macOS-only via Quartz window list

### Config
- Opt-in via phase 0 tool picker (`research` checkbox) plus the `research mode` consent row
- `max_queries = 3`, `max_fetches = 5`, `token_budget = 6000`
- Consent is one-time, stored in `~/.tokenpal/.consent.json` alongside other categories. No per-session re-prompt.

**Model:** reasoning models earn their keep here. `deepseek-r1:32b` for planner + synthesizer is the sweet spot. Main observation path stays on `gemma4`.

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

Registry size gotcha: `gemma4` reliably routes ~6 tools. Past 10, selection errors climb. When phase 2 lands (15+ tools total), we'll need per-intent tool subsetting: the brain picks a category first (`local`, `normal`, `focus`) then only passes that subset to the LLM.

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

1. **Tool subsetting trigger**: at what tool count do we need category-based subsetting in the prompt? Phase 2 guess: 12+. Measure before building.
2. **Agent mode public**: ship `/agent` as a debug command first, or polish UI before user-facing?

---

## Dependencies on Existing Work

- Registry (`@register_action`) already supports the `safe` / `requires_confirm` flags this plan needs.
- `ConversationSession` pattern extends naturally to `AgentSession`.
- `/ask` consent gate + sensitive-term filter is the prior art for phases 2c and 5.
- Memory.db at 0o600 is already the privacy-safe storage pattern phase 3 reuses.
- Textual modal patterns exist; agent confirm modal is additive.

No breaking changes required to existing senses, brain, or UI.
