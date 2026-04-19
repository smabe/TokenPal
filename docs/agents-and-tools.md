# Agents, Research, and the Tool Registry

TokenPal ships four layers of LLM-driven action on top of the observation buddy:

1. **Tools** — single-shot LLM-callable actions. Registry-backed, opt-in via a Textual picker, consent-gated for anything that touches the network.
2. **Inline research in conversation** — when you ask the buddy a "best X" / comparison / look-it-up question, it automatically calls `search_web` or the deeper `research` tool mid-conversation. No slash command, just ask naturally.
3. **Agent mode** (`/agent <goal>`) — multi-step tool-calling loop with a confirm gate, step cap, and token budget.
4. **Research mode** (`/research <question>`) — Claude-style plan → search → read → synthesize pipeline with citations and a 24h cache.

This doc covers each layer end-to-end: how to enable them, the command surface, how the conversation model routes to the right tool, rate limits, caches, usage stats, and the extension points for adding your own action.

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
| **Research** | `research_mode` flag, `search_web`, `fetch_url`, `research` | opt-in + `research_mode` consent |

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

## Inline research in conversation

The buddy can call tools mid-conversation without a slash command. Ask a question naturally and it routes to the right tool:

```
You: what's 47 * 83?
Buddy: [calls do_math(expr="47 * 83")] → 3901

You: hey what's on hacker news?
Buddy: [calls search_web(query="...")] → one snippet + clickable source link

You: hey what's the best fitness tracker for iPhone 17?
Buddy: [calls research(question="...")] → plans 3 queries, searches, reads 5 pages,
                                            synthesizes a cited bullet list + verdict
```

### How it picks the right tool

Three signals in the conversation system prompt (`PersonalityEngine.build_conversation_system_message`) steer the model:

- **Rule 6** lists the actual tool names loaded for the user (`research`, `search_web`, `do_math`, …) and says: "for 'best X' or comparison questions, call `research` — do NOT chain multiple `search_web` calls. For casual chat, just answer."
- **Tool descriptions** carry the narrower guidance:
  - `search_web`: "Single-query web lookup for ONE fact or ONE page. Do NOT use for comparisons or 'best X' questions."
  - `research`: "Deep research for comparison, recommendation, or 'best of' questions. Always use for 'best X', 'compare X vs Y', or anything that needs weighing multiple sources."
- **Rule 7** governs the reply format after `research` returns: 2-4 bullets of specific picks + a one-line verdict in the buddy's character voice.

### The `research` tool

A thin wrapper around `ResearchRunner` (the same pipeline `/research` uses), exposed as an LLM-callable action with a single `question` parameter.

```python
@register_action
class ResearchAction(AbstractAction):
    action_name = "research"
    parameters = {"type": "object", "properties": {"question": {"type": "string"}},
                  "required": ["question"]}
    rate_limit = RateLimit(max_calls=2, window_s=120.0)   # prevents loops
```

When called, it runs the full plan → search → fetch → synthesize pipeline and returns:

```xml
<tool_result tool="research" status="complete">
<answer>
- Garmin Forerunner 165 — 25-day battery, GPS [1]
- Fitbit Versa 4 — best iOS app [3]
Verdict: Forerunner 165 for marathon training [1].
</answer>
<sources>
[1] https://forbes.com/... - Article Title
[3] https://tomsguide.com/... - Another Title
</sources>
</tool_result>
```

The conversation model then paraphrases this into bullets + verdict in character, and the source URLs render as clickable links under the reply (via `ActionResult.display_urls`).

### Grounding guards

The synthesizer is prompted to cite every product with a `[N]` marker; post-hoc, `_strip_dangling_markers` drops any `[N]` that doesn't point to a real source. Any stripping emits a trace log:

```
  citations: 3 kept, 1 stripped (out-of-range — possible hallucination)
```

If fewer than `_THIN_POOL_THRESHOLD = 3` sources make it through fetching, the runner emits:

```
  warning: thin source pool (2 sources) — answer may be unreliable
```

### Enabling

```
/tools       # enable: research, search_web, fetch_url (all opt-in, in the Research section)
/consent     # grant: research_mode, web_fetches
# restart
```

Tool selection + links + citation guards work automatically once the tools are loaded and consent is granted. No other config required.

### Differences from `/research`

| | inline `research` tool | `/research` command |
|---|---|---|
| Entry point | buddy calls it mid-conversation | user types `/research <question>` |
| Output | paraphrased in character by the conversation model, ≤4 bullets + verdict | raw synthesized answer rendered verbatim |
| Model | conversation model handles planner + synthesizer | can swap to dedicated `planner_model` / `synth_model` in config |
| Cache | no (the conversation turn itself may vary in phrasing) | 24h question-hash cache in `memory.db` |
| Rate limit | 2 calls / 120s (per-session) | one at a time (`research_running` guard) |
| Best for | casual "hey buddy, what's the best X?" | deliberate deep-dive research questions |

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

See `docs/research-architecture.md` for the full design, invariants, and
extension points. Quick summary:

1. **Planner** (LLM call 1) — decompose the question into 1-3 search queries. Year injected into the prompt so "best X" queries favor recent sources.
2. **Search** (parallel, no LLM) — `asyncio.gather` across DuckDuckGo Lite only. Wikipedia's summary endpoint needs exact article titles and planner queries never match, so Wikipedia isn't in the research fan-out (it stays on `/ask`).
3. **Read** (no LLM) — two-stage fetch: newspaper4k with its own fetcher (primary, gets past TLS/header fingerprint gates on modern product-review sites), aiohttp + multi-extractor chain (fallback). 2MB read cap, 300-char minimum extraction, narrow identity-critical content filter, 4000-char excerpts handed to the synthesizer.
4. **Synthesize** (LLM call 2, thinking ON) — demands strict JSON matching `SYNTH_SCHEMA` (comparison kind with picks+verdict, or factual kind with answer+citations). Grammar-constrained on llama-server via `response_format: {"type":"json_schema",...}`, advisory on Ollama with prose fallback.
5. **Validate + render** — every pick's name must appear in the cited source's excerpt (substring or all-tokens-present, case-insensitive). Wrong citations repair to a matching source rather than drop. Thresholds: 0 verified → downgrade, 1 → single-pick render with "more context would help" caveat, 2+ → full comparison render.

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
max_fetches = 8
token_budget = 6000
per_search_timeout_s = 5.0
per_fetch_timeout_s = 8.0
cache_ttl_s = 86400             # 24h; zero disables the cache
```

Gated on the `research_mode` and `web_fetches` consent categories. Sensitive-app detection aborts before any network call.

---

## Cloud LLM (opt-in Anthropic)

`/research` (and the inline `research` tool) can optionally route some or all of its LLM calls through Anthropic. Off by default; toggle per-category with `/cloud`.

```
/cloud                      open the settings modal
/cloud enable <api-key>     store the key (~/.tokenpal/.secrets.json, 0o600) and flip on
/cloud disable              flip off (key retained)
/cloud forget               wipe key + disable
/cloud model <id>           claude-haiku-4-5 | claude-sonnet-4-6 | claude-opus-4-7
/cloud plan on|off          use cloud for the planner stage (opt-in)
/cloud search on|off        Sonnet drives web_search (snippets only, ~$0.10/run, Sonnet+ only)
/cloud deep on|off          Sonnet drives web_search + web_fetch (WARNING $1-3/run, Sonnet+ only)
/refine <follow-up>         re-synthesize the last /research against a follow-up (cloud)
```

### Three cloud modes

| mode | what cloud does | typical cost | when to use |
|---|---|---|---|
| **synth only** (default when `/cloud enable`) | Local plan+search+fetch → cloud synthesizes | ~$0.05/run (Haiku) to ~$0.15/run (Sonnet) | Better pick justifications and verdicts than local LLM can manage |
| **search** (`/cloud search on`) | Sonnet drives `web_search_20260209` — no fetch | ~$0.10-0.20/run | Fresh-web awareness without full page dumps |
| **deep** (`/cloud deep on`) | Sonnet drives `web_search_20260209` + `web_fetch_20260209` | **$1-3/run** (each fetch loads full page content into the tool-loop context and re-bills on every step) | Last resort: JS-heavy SPAs, bot-blocked sites, paywalled previews the local pipeline can't touch |

If both `deep` and `search` are on, **deep wins**. Both require a Sonnet 4.6+ model — Haiku doesn't support the dynamic-filtering web tools.

### What crosses the wire

Only `/research` paths. Never observations, conversation turns, planner (unless you flip `/cloud plan on`), `/ask`, or idle-tool rolls — those stay local. Payload is the question plus either bundled local source excerpts (synth-only mode) or just the question (search/deep modes; Sonnet fetches server-side).

### Fallback + warnings

Any `CloudBackendError` (auth, rate limit, network, timeout, `no_credit` for an unfunded workspace) falls back to local synth with identical prompt + schema. Deep-mode activation prints a cost warning; the modal checkbox carries the warning in its label.

### /refine

`/refine <follow-up>` re-runs the synth stage against the last research's cached sources with the follow-up included. Requires cloud. Refuses after a deep-or-search run because those modes don't cache source excerpts (Anthropic read pages server-side) — re-run `/research` with your refined question instead.

### Cache

Cache keys separate by mode: `""` (local), `"search:"`, `"deep:"`. Same question run in different modes keeps distinct entries so follow-ups see their own provenance. 24h TTL per mode.

Full architecture + cost model + design rationale in [`docs/research-architecture.md`](research-architecture.md) and `plans/shipped/cloud-native-web-search.md`.

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
| `/refine <follow-up>` | re-synthesize the last /research against a follow-up (cloud) |
| `/cloud` | open the Anthropic cloud-LLM settings modal |
| `/cloud enable <key>` | store key + flip on; `/cloud disable`, `/cloud forget`, `/cloud model <id>` |
| `/cloud plan on`\|`off` | route /research planner through cloud (opt-in) |
| `/cloud search on`\|`off` | Sonnet drives web_search only (~$0.10/run, Sonnet+) |
| `/cloud deep on`\|`off` | Sonnet drives web_search + web_fetch (WARNING $1-3/run, Sonnet+) |
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
| single tool call | `gemma4` / `qwen3:8b` | `qwen3:14b` | simple routing, ≤8 tools |
| inline research (conversation) | `qwen3:8b` | `qwen3:14b` | tool routing + synthesis on the same model |
| agent loop | `gemma4:26b` | `qwen2.5:32b` | multi-step planning degrades below 26B |
| research planner/synth | `qwen2.5:32b` / `qwen3:14b` | `deepseek-r1:32b` | reasoning helps decomposition + citation fidelity |
| research reader | no LLM needed | — | reader is pure HTTP fetch + trafilatura |

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
- **Buddy says "let me search" but doesn't actually call a tool** — the tool isn't loaded. Check `/tools list` and make sure `search_web` / `research` are enabled, then restart. The conversation system prompt only lists tools actually resolved at startup, so if a tool is absent the model may narrate without calling.
- **Buddy recommends stale/old products after `research`** — this happens when the synthesizer fills gaps from training data. The log line `citations: N kept, M stripped` is the smoking gun. Workarounds: (a) check for a `thin source pool` warning — if fewer than 3 sources landed, the answer is single-source-biased; (b) try a larger planner/synth model in `[research]`; (c) the JSON-output synthesizer (parked; see recent plan files) is the real fix.
- **Research returns nothing and logs `NO_SOURCES`** — all fetches failed. Common causes: offline, all five DDG Lite results were JS-heavy SPAs that trafilatura couldn't extract, or the sensitive-term filter tripped on every result. Enable `--verbose` to see per-URL extract failures at DEBUG level.
