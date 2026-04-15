# Agents, Research, and the Tool Registry

TokenPal ships three layers of LLM-driven action on top of the observation buddy:

1. **Tools** — single-shot LLM-callable actions. Registry-backed, opt-in via a Textual picker, consent-gated for anything that touches the network.
2. **Agent mode** (`/agent <goal>`) — multi-step tool-calling loop with a confirm gate, step cap, and token budget.
3. **Research mode** (`/research <question>`) — Claude-style plan → search → read → synthesize pipeline with citations and a 24h cache.

This doc covers each layer end-to-end: how to enable them, the command surface, what's new in Phase 6 (tool discovery, rate limits, caches, usage stats), and the extension points for adding your own action.

---

## Tool Registry

Every LLM-callable action subclasses `AbstractAction` and is discovered at startup through the `@register_action` decorator. Each action declares:

| field | purpose |
|---|---|
| `action_name` | identifier the LLM sees in `tools=[...]` |
| `description` | one-line hint for routing |
| `parameters` | JSON Schema for the arguments |
| `platforms` | `("windows", "darwin", "linux")` subset |
| `safe` | read-only / no side effects? |
| `requires_confirm` | ask the user before firing? |
| `rate_limit` | optional `RateLimit(max_calls, window_s)` |
| `cacheable` | skip the in-run cache if False |

The four defaults (`timer`, `system_info`, `open_app`, `do_math`) are always on. Everything else is opt-in through `/tools`.

### Enabling opt-in tools

```
/tools                  # opens the Textual picker (grouped by section)
/tools list             # plain-text fallback for headless / console overlay
/tools describe <name>  # blurb, section, platforms, flags, rate limit
```

The picker writes to `[tools] enabled_tools` in `config.toml`. TokenPal resolves tools once at startup, so the modal reminds you to restart after saving. Network-touching tools also require a `/consent` category checkbox (see below).

### Consent

One-time categories, stored in `~/.tokenpal/.consent.json` at `0o600`:

```
/consent                  # open the category picker
```

| category | covers |
|---|---|
| `web_fetches` | all network-reading tools (currency, weather forecast, CoinGecko, etc.) and `/ask` |
| `location_lookups` | geocoding for weather/zipcode |
| `external_keyed_apis` | tools that require an env-var API key (TMDB, Finnhub, etc.) |
| `research_mode` | `/research` command specifically |

No per-session re-prompt. Revoke by reopening the picker and unchecking.

### Sections

The catalog (`tokenpal/actions/catalog.py`) groups tools into six sections. Every entry carries a `kind` discriminator (`default|local|utility|focus|agent|research`) used by `/tools describe` and by future subsetting heuristics.

| section | example tools | gated on |
|---|---|---|
| **Default** | `timer`, `system_info`, `open_app`, `do_math` | always on |
| **Local** | `read_file`, `grep_codebase`, `git_log`, `git_diff`, `git_status`, `list_processes`, `memory_query` | opt-in only |
| **Utilities** | `convert`, `timezone`, `sunrise_sunset`, `moon_phase`, `currency`, `weather_forecast_week`, `pollen_count`, `air_quality`, `random_fact`, `joke_of_the_day`, `word_of_the_day`, `on_this_day`, `random_recipe`, `trivia_question`, `sports_score`, `crypto_price`, `book_suggestion` | opt-in + `web_fetches` for network ones |
| **Focus** | `pomodoro`, `stretch_reminder`, `water_reminder`, `eye_break`, `bedtime_wind_down`, `hydration_log`, `habit_streak`, `mood_check` | opt-in |
| **Agent** | `agent_mode` flag | opt-in |
| **Research** | `research_mode` flag, `search_web`, `fetch_url` | opt-in + `research_mode` consent |

### Rate limits

Add a `rate_limit` ClassVar to enforce a rolling-window cap on any action:

```python
from tokenpal.actions.base import AbstractAction, ActionResult, RateLimit

@register_action
class CryptoPrice(AbstractAction):
    action_name = "crypto_price"
    rate_limit = RateLimit(max_calls=5, window_s=60.0)
    safe = True
    requires_confirm = False
    ...
```

The shared `ToolInvoker` enforces this before calling `execute`. Exceeding the window returns `ActionResult(success=False, output="rate limit: N calls/Ws exceeded")` — fail-fast, never sleeps or queues. State is process-local per agent run.

### Usage stats

Every invocation is logged to `memory.db` in the `tool_calls(ts, tool_name, duration_ms, success)` table (`0o600`, owner-only). Read it back through `MemoryStore.tool_usage_counts(since_days=...)`:

```python
from tokenpal.brain.memory import MemoryStore
m = MemoryStore(Path("~/.tokenpal/memory.db").expanduser())
m.setup()
print(m.tool_usage_counts(since_days=7))
# {"memory_query": 3, "git_log": 2, ...}
```

Future polish: surface these stats back to the buddy so it can riff on which tools you lean on. (Parked.)

---

## Agent mode: `/agent <goal>`

Run a multi-step tool-calling loop toward a natural-language goal.

```
/agent summarize what I worked on today
/agent which process is eating my CPU, and is it a me-problem or a system-problem
/agent find the last three times I edited tokenpal/brain/ and describe the theme
```

### What happens

1. Brain switches to `BrainMode.AGENT`. Observations and freeform thoughts are suppressed for the duration.
2. Optional model swap: `[agent] model = "qwen2.5:32b"` (recommended for 40GB-class GPUs; `gemma4:26b` the floor for reliable multi-step routing). Swap reverts when the run ends.
3. Loop: LLM call with `tools=[...]` → execute tool calls → feed results back → repeat. Termination conditions:
   - Model returns text with no tool calls → `COMPLETE`
   - Step cap hit (default 8) → `STEP_CAP` with forced final synthesis
   - Token budget hit (default 12k) → `TOKEN_BUDGET` with forced synthesis
   - Per-step timeout (default 45s) → `TIMEOUT`
   - Sensitive-app detected mid-run → `SENSITIVE`, abort
   - User denies a `requires_confirm` tool → `DENIED`, forced synthesis
   - Exception inside a tool → logged as an error step, loop continues
4. Every tool call logs to the chat log as `→ tool(args)` / `← result` (or `← (cached) ...` on a repeat). The final synthesis bubbles up as speech.

### In-run cache

Identical tool calls within a single run return the cached result instead of re-executing. Key: `(tool_name, sorted-args-json)`. Skipped for:

- Tools with `requires_confirm = True` — user might want to re-confirm
- Tools with `cacheable = False` — time-sensitive or side-effectful

The cache lives on the `AgentRunner` and is reset at the start of each `run()`. Not persisted across runs.

### Config

```toml
[agent]
model = ""                      # empty = fall back to [llm] model_name
max_steps = 8
per_step_timeout_s = 45.0
token_budget = 12000            # soft cap (Ollama sometimes reports 0)
```

### Notes on model choice

- ✅ `qwen2.5:32b` — BFCL ≈ GPT-4o-mini for tool calling, the sweet spot
- ✅ `llama3.3:70b` — most robust if you have the VRAM
- ⚠️ `gemma4:26b` — OK at <20 tools, degrades past that
- ❌ `deepseek-r1:32b` — **do not use for the agent loop.** R1 wraps tool calls inside `<think>` tags and frequently emits JSON inside reasoning instead of the `tool_calls` field. Same failure as Qwen3.

---

## Research mode: `/research <question>`

A Claude-style research pipeline with citations.

```
/research best USB-C docks for an M4 Mac Mini in 2025
/research why did the Rust async stabilization take so long
/research how does Textual compute width for a bordered widget
```

### Pipeline

1. **Planner** (LLM call 1, temp ≤ 0.3) — decompose the question into 1–5 search queries. Few-shot prompted to avoid over-decomposition (single-hop ≠ 5 queries).
2. **Search** (parallel, no LLM) — `asyncio.gather(..., return_exceptions=True)` across DuckDuckGo IA, Wikipedia REST, and optional Brave. Per-backend `asyncio.Semaphore` prevents spurious 429s. Results deduped by URL.
3. **Read** (LLM call 2, optional) — for each promising hit, fetch full page via `fetch_url` (trafilatura → readability-lxml fallback). 500KB size cap, sensitive-term filter. No JS.
4. **Synthesize** (LLM call 3) — sources rendered as numbered list `[1] <url>\n<excerpt>` placed *before* the question in the user turn (recency bias favors citation fidelity). Post-hoc regex validation strips dangling `[N]` markers that don't match any source.

### 24h cache

Identical questions within `cache_ttl_s` (default 86400 = 24h) return the previous synthesis with a `(cached Nh ago)` prefix. Key: `sha256(question.strip().lower())`. Stored in `memory.db` under `research_cache(question_hash, question, answer, sources_json, created_at)`.

Bypass by editing the question (adds a word, punctuation, whatever) — keys are lowercase-trimmed exact match. A `--fresh` flag is parked for a future polish pass.

### Config

```toml
[research]
planner_model = ""              # empty = reuse [llm] model_name
synth_model = ""                # both can use deepseek-r1:32b — no tool calls, no <think>-tag problem
reader_model = ""               # reader stays on qwen2.5:32b or similar (no reasoning benefit)
max_queries = 3
max_fetches = 5
token_budget = 6000
per_search_timeout_s = 5.0
per_fetch_timeout_s = 8.0
cache_ttl_s = 86400             # 24h; zero disables the cache
```

Gated on the `research_mode` and `web_fetches` consent categories. Sensitive-app detection aborts before any network call.

---

## Slash-command reference

| command | what it does |
|---|---|
| `/tools` | open the Textual tool-picker modal |
| `/tools list` | plain-text list of tools with on/off marks |
| `/tools describe <name>` | full metadata: blurb, section, kind, consent, platforms, safe/confirm, rate_limit, cacheable |
| `/consent` | open the consent-category picker |
| `/agent <goal>` | run the multi-step agent loop |
| `/research <question>` | run the plan-search-read-synthesize pipeline |
| `/math <expr>` | evaluate arithmetic without the LLM (AST walker; safe) |
| `/ask <question>` | one-shot web search (DuckDuckGo IA + Wikipedia fallback) |

---

## Extension points

### Adding a new action

Drop a file under `tokenpal/actions/<your_tool>.py` or under one of the subpackage folders (`local/`, `utilities/`, `focus/`, `research/`). Register with `@register_action` and declare the class variables. It appears in the registry on next startup.

```python
from tokenpal.actions.base import AbstractAction, ActionResult, RateLimit
from tokenpal.actions.registry import register_action

@register_action
class WhatsForDinner(AbstractAction):
    action_name = "whats_for_dinner"
    description = "Suggest a random meal from a built-in list."
    parameters = {"type": "object", "properties": {}}
    safe = True
    requires_confirm = False
    rate_limit = RateLimit(max_calls=5, window_s=60.0)

    async def execute(self, **_kwargs) -> ActionResult:
        import random
        meals = ["tacos", "ramen", "fried rice", "big salad"]
        return ActionResult(output=random.choice(meals))
```

Add a `CatalogEntry` to one of the sections in `tokenpal/actions/catalog.py` so the picker can find it:

```python
UTILITIES_SECTION = CatalogSection(
    ...
    entries=(
        ...,
        CatalogEntry("whats_for_dinner", "Random meal idea.", kind="utility"),
    ),
)
```

Network-touching actions should set `consent_category="web_fetches"` on the entry and check `has_consent(Category.WEB_FETCHES)` inside `execute` before hitting the network.

### Custom system prompts for the agent

The agent runner accepts a `system_prompt` kwarg in tests; override by subclassing `AgentRunner` or composing a replacement `Brain._build_runner`. The default prompt lives at the bottom of `tokenpal/brain/agent.py` (`_DEFAULT_SYSTEM_PROMPT`).

### Research backends

Backends are `tokenpal/senses/web_search/client.py` — `BackendName` Literal + `search()` dispatch. Add a new backend by extending the literal, adding a client function, and wiring it into the per-backend semaphore table in `tokenpal/brain/research.py`.

---

## Model recommendations by mode

| mode | min viable | recommended | notes |
|---|---|---|---|
| single tool call | `gemma4` | `gemma4` | simple routing, ≤8 tools |
| agent loop | `gemma4:26b` | `qwen2.5:32b` | multi-step planning degrades below 26B |
| research planner/synth | `qwen2.5:32b` | `deepseek-r1:32b` | reasoning helps decomposition + citation fidelity |
| research reader | `qwen2.5:32b` | `qwen2.5:32b` | reader doesn't benefit from reasoning |

Registry size thresholds (BFCL / ToolBench evals through mid-2025):

- `gemma4` (9B class): >90% routing at 8 tools, ~70% at 16, collapses past 24
- `qwen2.5:7b`: reliable to ~12 tools
- `qwen2.5:14b/32b`: reliable to 20–30
- `llama3.3:70b`: 40+ tools without major degradation

Phase 2 ships 15+ tools total, past the gemma4 cliff. If you enable the full utility set, bump the agent model to qwen2.5:32b or higher.

---

## Troubleshooting

- **"/agent is off. Enable 'agent_mode' in /tools and restart."** — the picker writes the flag to `config.toml`; restart TokenPal to actually instantiate the agent bridge.
- **"/research is off..."** — same, plus you need the `research_mode` and `web_fetches` consent checkboxes.
- **Agent times out on step 1 with a big model** — model swap does cold load. First run after a swap is slow; subsequent runs hit the loaded model. Bump `per_step_timeout_s` or pre-load with `ollama run qwen2.5:32b "warmup"`.
- **Tool call fires with `{}` arguments when it shouldn't** — Ollama occasionally returns `arguments=""` instead of a dict. The runner json.loads with try/except; if the model is consistently emitting bad JSON, try a different model (some fine-tunes regress on tool traces).
- **Research returns an empty answer with sources listed** — synthesizer hit the token budget before writing. Increase `[research] token_budget`, or drop `max_fetches` so fewer excerpts land in the prompt.
- **Cached research answer is stale after an event you care about** — edit the question to bypass the cache, or set `[research] cache_ttl_s = 0` to disable. The plan logs `> research: ... (cached)` when a hit fires, so you can always tell.
- **Rate limit tripped inside an agent run** — the invoker's state is per-run, not per-session. Start a new `/agent` to reset, or remove the `rate_limit` ClassVar temporarily.
