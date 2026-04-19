# research-source-upgrade

## Goal
Expand `/research` source-grabbing with a Tavily+Haiku default cloud tier, free-tier fallbacks (Brave, HN, Stack Exchange), and a smart planner that routes queries to the best backend. Cuts cloud deep-mode reliance by ~80% while keeping substring-grounded validation and `/refine` working.

## Non-goals
- Playwright / headless-browser retry for JS SPAs. Deferred — cloud deep mode remains the escape hatch. Revisit post-ship with real telemetry on SPA-miss rate.
- Google CSE, Exa, SerpAPI, Kagi, Mojeek. Tavily covers the commercial niche; no need for two.
- Wikipedia in the fan-out. Slug problem unchanged. Smart planner routes `"what is X"` factuals to Wikipedia REST via the existing `/ask` path if needed; no new scraping.
- Reddit JSON. Cool but scope-expansive — parking lot.
- Replacing cloud deep mode. It stays as the premium tier for rtings-class SPAs and agentic multi-hop research.
- Replacing DDG. Stays as the zero-cost default when no cloud keys are configured.
- Replacing local Qwen3 synth. Tavily+Haiku is a new tier, not a forced upgrade.
- archive.org Wayback fallback. Small, high-signal — parking lot for a follow-up issue.

## Files to touch

### Phase 1: Tavily backend + cloud search tier

**Architectural decisions locked:**
- Command surface: **two-level `/cloud <backend> <action>`** (Q1=A). Refactors `app.py:1256-1478` flat dispatcher into nested structure. Backward-compat shims for existing `/cloud enable/disable/forget/model` (sugar for `/cloud anthropic ...`).
- Preloaded content: **new `SearchResult.preloaded_content: str = ""` field** (Q2). Never truncated — keeps full Tavily article body for synth. Existing `text` field stays as the 500-char snippet for logging/display.

**Files:**
- `tokenpal/senses/web_search/client.py` — add `TavilyBackend` class, extend `BackendName` literal to `"tavily"`, add `preloaded_content: str = ""` field to `SearchResult` dataclass (NOT truncated — stores full Tavily content verbatim), extend `search_many` dispatcher to route to Tavily, add `tavily` to `_BACKEND_CONCURRENCY`.
- `tokenpal/senses/web_search/tavily.py` — NEW. Isolated Tavily HTTP client (`api.tavily.com/search`), reads key from secrets store, handles `search_depth=advanced`, returns list of `SearchResult` with `preloaded_content` populated.
- `tokenpal/config/secrets.py` — refactor JSON structure from `{"cloud_key": "..."}` → `{"anthropic_key": "...", "tavily_key": "...", "brave_key": "..."}`. Add `get_tavily_key`/`set_tavily_key`/`forget_tavily_key`. Extend `fingerprint()` to recognize `sk-ant-`, `tvly-`, and Brave key formats. **Migration shim**: on read, detect legacy `cloud_key` field and rewrite as `anthropic_key` on first write; keep reading both until the migration lands on disk.
- `tokenpal/config/schema.py` — add `CloudSearchConfig` dataclass at line 372+ alongside `CloudLLMConfig`, wire into `TokenPalConfig` at line 411. Fields: `enabled: bool = False`, `backend: Literal["tavily"] = "tavily"`, `search_depth: Literal["basic", "advanced"] = "advanced"`, `max_results: int = 6`.
- `tokenpal/brain/research.py` — three targeted edits:
  - `_search_all` (line 313-333): replace hardcoded `"duckduckgo"` at line 320 with per-query dispatch driven by `PlannedQuery.backend` (default `"duckduckgo"` when cloud_search disabled, `"tavily"` when enabled with no explicit override from planner).
  - `_read` (line 376-402): when `hit.preloaded_content` is non-empty, use it directly as excerpt and skip `self._fetch()`. Import `contains_sensitive_content_term` from `tokenpal.actions.research.fetch_url` (or hoist it to a shared module) and run it on the preloaded content before excerpt assignment; filtered content → skip the source entirely, same as fetch path.
  - `ResearchSession` dataclass (line 75-82): add `warnings: list[str] = field(default_factory=list)`. Thin-pool warning at line 196-204 appends to `session.warnings` in addition to `self._log`. `_format_result` at research_action.py serializes warnings into the `<tool_result>` XML so the transcript surfaces them.
- `tokenpal/actions/research/research_action.py` — plumbs `cloud_search` config through to `ResearchRunner`; extends `_format_result` to emit `<warnings><warning>...</warning></warnings>` block when `session.warnings` non-empty.
- `tokenpal/app.py` — refactor `/cloud` dispatcher at lines 1256-1478 from flat `parts[0]` matching to two-level `parts[0]=<backend>, parts[1]=<action>`. Backends: `anthropic` (existing cloud_llm), `tavily` (new), `brave` (phase 2). Shim: bare `/cloud enable/disable/forget/model` continues to work as `/cloud anthropic ...` with a deprecation log line. New subcommands: `/cloud tavily enable <key>`, `/cloud tavily disable`, `/cloud tavily forget`, `/cloud tavily status`. First-enable warning bubble about commercial-surface + privacy.
- `tokenpal/ui/cloud_modal.py` — extend the modal to include backend picker (radio: Anthropic / Tavily / Brave) and route key entry to the selected backend. Don't block Phase 1 shipping if modal changes are out-of-scope — command-line path first, modal can follow.
- `config.default.toml` — new `[cloud_search]` section with defaults off + comments. Also bump existing `[research] max_queries = 3` → `5` to give Tavily-backed runs more surface area on multi-hop questions.
- `tests/test_web_search.py` — extend existing file (NOT new `test_web_search_backends.py`): add Tavily mock-response tests, `preloaded_content` field round-trip, sensitive-term filter on preloaded content.
- `tests/test_research.py` — add session.warnings propagation test, `_read` short-circuit test for Tavily-sourced hits.
- `tests/test_secrets.py` (likely exists, check) — add legacy `cloud_key` migration test, multi-backend-key coexistence test.

### Phase 2: Brave backend (free-tier)
- `tokenpal/senses/web_search/client.py` — finish `BraveBackend.search()` (stub already at line 231-245). Implement Web Search API call (`api.search.brave.com/res/v1/web/search`), parse `web.results[].{title, url, description}`. 2k/month free tier. Support `search_many` via `count=N` param.
- `tokenpal/config/secrets.py` — `get_brave_key`/`set_brave_key`/`forget_brave_key` using the multi-key JSON structure landed in Phase 1.
- `tokenpal/app.py` — add `/cloud brave enable <key>`, `/cloud brave disable`, `/cloud brave forget`, `/cloud brave status` under the two-level dispatcher from Phase 1.
- `tokenpal/ui/cloud_modal.py` — Brave option in backend picker (if modal update lands).
- `tests/test_web_search.py` — mock Brave responses, verify parse + `_truncate` + env-var vs explicit-key precedence.

### Phase 3: Topic-specific free backends
- `tokenpal/senses/web_search/hn.py` — NEW. Wraps Algolia HN API (`hn.algolia.com/api/v1/search?query=...`). Returns up to N hits as `SearchResult`. Reuses the pattern from `world_awareness` sense — factor out common code if needed.
- `tokenpal/senses/web_search/stackexchange.py` — NEW. Stack Exchange API 2.3 (`api.stackexchange.com/2.3/search/advanced?...&site=stackoverflow`). Backoff/quota-aware (IP-keyed anonymous quota is 300/day — fine for `/research` volume).
- `tokenpal/senses/web_search/client.py` — extend dispatch, add to `BackendName` literal.

### Phase 4: Smart planner routing
- `tokenpal/brain/research.py` — `_plan` prompt extended: planner now emits `{"query": "...", "intent": "...", "backend": "tavily|brave|ddg|hn|stackexchange|wikipedia"}`. New helper `_route_query` validates backend choice against availability (falls back to ddg if chosen backend is unconfigured). `_search_all` dispatches per-hit rather than per-run.
- `tokenpal/brain/research.py` — `_PLANNER_PROMPT` gets a routing-hint section: tech-how-to → HN/StackExchange, product comparisons → tavily/brave, factual-lookup → wikipedia, generic → default backend.
- Tests: golden-query routing test (`tests/test_research_planner.py`) — pin expected backend for a handful of representative queries.

### Phase 5: Docs + telemetry
- `docs/research-architecture.md` — add "Source backends" section documenting the fan-out, routing rules, cost per backend, privacy surface per backend. Update the Stage 2 (Search) + Stage 3 (Read) descriptions.
- `tokenpal/brain/research.py` — add a log line at end of each run: `"research: mode=<backend-mix> sources=<N> cost=<est>"`. Uses same session-log path as existing research warnings. Gives us data to decide whether Playwright is worth adding later.
- `CLAUDE.md` — update `/research` section to cover the new tiers and `/cloud tavily` command family.

## Failure modes to anticipate
1. **Tavily rate limit or outage.** Fall back to DDG + local extractor chain. Must not break `/research` when Tavily 500s or times out. Same `CloudBackendError` silent-fallback pattern the existing cloud synth uses.
2. **Planner picks an unconfigured backend.** If config has no Brave key but planner emits `backend=brave`, `_route_query` must downgrade silently to default (DDG) — no hard failure, no user-facing error.
3. **Tavily returns fewer than `_THIN_POOL_THRESHOLD=3` results.** With Tavily on, the extractor-chain safety net is gone. Decision: **refetch with DDG automatically** and emit a visible warning bubble in the pal's transcript (e.g. `"tavily thin — topped up from ddg"`) so the user knows coverage is degraded. Always on, not a config flag. Low-frequency event; double-cost impact is negligible.
4. **Schema drift in Tavily response.** Their API is in active development. Defensive parse — missing `content` field → skip hit with warning, don't crash. Version-pin the API base URL (`api.tavily.com/search`) and document in the backend.
5. **Sensitive-content filter bypass.** Tavily hands us pre-extracted text. `contains_sensitive_content_term` must still run on every `content` field before it enters `sources_block`. Audit in `_read` short-circuit path.
6. **Cost headroom.** Planner is getting bumped to `max_queries=5`, so new worst case is 5 advanced Tavily calls = 10 credits ≈ $0.08/run. Tavily's 1k free credits/month → ~100 worst-case runs, realistically ~165-250 since most queries emit 2-3 plans. No per-run credit budget needed; document the math in the status output so users can see what their quota buys.
7. **Secrets file corruption.** `set_tavily_key` on a broken `.secrets.json` — currently we just overwrite? Check. If there's a merge behavior for Anthropic key, mirror it. Tests cover the "Anthropic already set, add Tavily" path.
8. **Brave v1 API response shape.** Haven't shipped this before. Docs say `web.results[].{title, url, description}`. Confirm in tests before wiring into dispatch.
9. **Stack Exchange anonymous quota (300/day, IP-keyed).** Fine for personal use but shared IP / coffee shop could exhaust. Add 429 handling → silent fallback to DDG.
10. **HN Algolia query syntax.** Their API supports complex filter strings but we want plain-text search. Confirm `query=<text>` behaves as expected; avoid accidentally hitting `tags=` syntax from the planner.
11. **Planner backend field hallucinations.** LLM emits `"backend": "bing"` or typos like `"ddg"`. `_route_query` normalizes + validates against the literal.
12. **Privacy story bloat.** Three commercial surfaces (Anthropic, Tavily, Brave) plus free-but-logged (HN, Stack Exchange) all leak query text. `/cloud status` must clearly show what's enabled and where queries go. New `--privacy` flag or section in output.
13. **Validation still works with Tavily content.** `_validate_picks` does substring match on excerpts. Tavily's `content` field is cleaner than our extractor output, which is *good*, but shorter too — verify the 300-char threshold still makes sense (might want to drop it for Tavily-backed sources since we trust the extraction).
14. **Config migration.** Existing users have no `[cloud_search]` section. Loader must default it to disabled, not crash. Test on a pre-upgrade `config.toml`.
15. **Legacy secrets migration.** Existing `/cloud enable` users have `~/.tokenpal/.secrets.json = {"cloud_key": "sk-ant-..."}`. Post-upgrade the file must auto-migrate to `{"anthropic_key": "sk-ant-..."}` on first read, without losing the key. Read path recognizes both shapes; write path only emits the new shape. Test: write legacy file by hand, run `/cloud status`, confirm key still resolves + fingerprint matches.
16. **Fingerprint helper key-format coverage.** Current `fingerprint()` at `secrets.py:80-86` only handles `sk-ant-` shape. Tavily keys are `tvly-<32+chars>`, Brave keys are alphanumeric `BSA...`-ish. Helper must not crash or return garbage on non-Anthropic shapes; extend to truncate-safely regardless of prefix.
17. **Modal UI lag vs CLI.** Two-level dispatch lands in app.py first. `cloud_modal.py` only supports one backend today. Plan ships Phase 1 even if modal update is deferred — as long as CLI works, users have a path. Modal extension can be its own follow-up issue.
18. **Command back-compat regressions.** Existing shell history + muscle memory for `/cloud enable sk-ant-...` must keep working after the refactor. Shim path tests: all six existing subcommands (`enable`, `disable`, `forget`, `model`, `plan`, `deep`, `search`) still dispatch correctly when the first word isn't a backend name.

## Done criteria
- [ ] `/cloud tavily enable <key>` stores the key at `~/.tokenpal/.secrets.json` (0o600), flips `[cloud_search].enabled = true`, confirms via fingerprint.
- [ ] `/research <question>` with Tavily enabled routes through Tavily for search+fetch, skips the local extractor chain, and feeds pre-extracted content to Haiku synth. End-to-end latency drops measurably (target: <8s on typical query vs current 15-25s).
- [ ] Tavily outage falls back silently to DDG + local fetch — `/research` answer still lands.
- [ ] Tavily thin-pool (<3 results) auto-tops-up from DDG and surfaces a one-line warning bubble in the transcript so the user sees the degraded-coverage signal.
- [ ] Brave backend works standalone (`backend=brave` in `search_many` returns results).
- [ ] HN + Stack Exchange backends return plausible results for canonical queries (`"python GIL"` → SE top hits; `"show HN rust"` → HN hits).
- [ ] Smart planner's routing decisions land correctly on a 10-query golden test (Wikipedia factuals, SE technical, HN tech-news, Tavily/Brave general, DDG fallback when others unconfigured).
- [ ] Substring-grounded `_validate_picks` still works on Tavily-sourced content. `/refine` still works (excerpts present).
- [ ] `/cloud status` shows all configured search-layer surfaces with fingerprints.
- [ ] `docs/research-architecture.md` updated with backend routing table + cost-per-tier table.
- [ ] Per-run telemetry log line emitted (`mode=<backend-mix> sources=<N>`) so we can measure actual Tavily vs fallback rates post-ship.
- [ ] Tests pass: backend parsers (Tavily, Brave, HN, SE), planner routing, sensitive-term filter on Tavily content, config migration from pre-upgrade toml.
- [ ] Legacy-secrets migration: `{"cloud_key": "sk-ant-..."}` auto-upgrades to `{"anthropic_key": "..."}` on first read; existing users' `/cloud status` still shows the right Anthropic fingerprint after upgrade.
- [ ] Back-compat: `/cloud enable <key>`, `/cloud disable`, `/cloud forget`, `/cloud model <id>`, `/cloud plan on/off`, `/cloud search on/off`, `/cloud deep on/off` all continue to work as sugar for `/cloud anthropic ...` with a deprecation log line (not a user-visible error).

## Parking lot
(empty at start — append "ooh shiny" thoughts that surface mid-work for later)
