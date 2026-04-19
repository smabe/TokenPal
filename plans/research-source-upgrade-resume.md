# research-source-upgrade — resume prompt for next session

**Current state**: Phases 1 + 2 shipped on `main`. Phases 3, 4, 5 remain.
Full plan lives at `plans/research-source-upgrade.md`; reference that for
the authoritative scope / done criteria / failure modes.

## What's on `main` as of the last session

| Commit | Summary | Files |
|---|---|---|
| `13ced79` | Phase 1: Tavily+Haiku tier | 18 files, +1308 / -88 |
| `be3b363` | Phase 1 simplify: fold tavily params into CloudSearchConfig | 6 files, +25 / -29 |
| `18736c5` | Phase 2: Brave backend + shared HTTP helper | 7 files, +464 / -50 |

Tests: **1,244 passing** / 2 skipped / 0 failing.

### Phase 1 delivered (shipped)
- `[cloud_search]` config section + `CloudSearchConfig` dataclass
- Secrets refactor to multi-key (`anthropic_key`, `tavily_key`, `brave_key`) with legacy `cloud_key` auto-migration
- `SearchResult.preloaded_content` field (never truncated) for Tavily-class backends
- `TavilyBackend` + `tokenpal/senses/web_search/tavily.py`
- `ResearchRunner` changes: per-query backend dispatch, `_read` short-circuits on preloaded content with sensitive-term filter, `ResearchSession.warnings` list, thin-pool DDG top-up
- `research_action._format_result` emits `<warnings>` XML block
- `/cloud` two-level dispatcher (`/cloud anthropic|tavily|brave <action>`) with legacy flat-subcommand shims
- `max_queries` bumped `3 → 5`

### Phase 2 delivered (shipped)
- `BraveBackend` finished — no more `NotImplementedError`
- `tokenpal/senses/web_search/brave.py` (GET Brave Web Search API)
- `tokenpal/senses/web_search/_http.py` — shared `http_json()` helper used by both `tavily.py` and `brave.py`
- `/cloud brave [enable|forget|status]` (no `disable` — Brave has no runtime flag, presence of key = active)
- Aggregate `/cloud` status lists all three backends

## What still needs to ship

### Phase 3: topic-specific free backends
New files:
- `tokenpal/senses/web_search/hn.py` — wrap Algolia HN API. Pattern already in `tokenpal/senses/world_awareness/hn_client.py` — reuse, don't reinvent.
- `tokenpal/senses/web_search/stackexchange.py` — SE API 2.3, 300/day IP-keyed anonymous quota. 429 → silent fallback to DDG.

Wiring:
- Extend `BackendName` literal in `client.py` with `"hn"` + `"stackexchange"`.
- Extend `_BACKEND_CONCURRENCY` in `research.py` (SE is quota-limited — set low, e.g. 1).
- `search()` + `search_many()` dispatch branches for both.
- Tests in `tests/test_web_search.py`: parse + filter + network-error paths.

No config section needed — both are keyless. No `/cloud` subcommand either.

### Phase 4: smart planner routing
Heavier lift — touches the LLM prompt.

In `tokenpal/brain/research.py`:
- Extend `_PLANNER_PROMPT` (line ~1140 after the earlier bumps) with a routing-hint block:
  - `"how does X work"` factual → `wikipedia`
  - tech how-to → `stackexchange`
  - product comparisons → `tavily` if configured else `brave` else `ddg`
  - "show HN" / hacker news → `hn`
  - generic → default backend
- `PlannedQuery.backend` is already `str` (set during parsing). No schema change needed — `_resolve_backend` at line ~330 normalizes + falls back.
- **Important**: `_resolve_backend` currently only knows about backends in `_BACKEND_CONCURRENCY`. When adding HN + SE there, `_resolve_backend` automatically accepts them. Verify the typo-guard behavior stays right.
- Tests: golden-query routing in `tests/test_research_planner.py` (new file). 10 canonical queries, pinned backend per query. Mock `_plan` LLM output; assert `session.queries[i].backend` matches expectation.

### Phase 5: docs + telemetry
- `docs/research-architecture.md` — update Stage 2 (Search) + Stage 3 (Read) sections. Add a "Source backends" table (backend / cost / privacy surface / when-used).
- `tokenpal/brain/research.py` — add end-of-run telemetry log: `"research: mode=<backend-mix> sources=<N>"`. Lets us measure Tavily vs fallback rates post-ship and decide if Playwright is worth adding later.
- `CLAUDE.md` — update `/research` section with new tiers + `/cloud tavily|brave` command family.

## Key architecture notes for the next session

1. **`CloudSearchConfig` is Tavily-specific today.** If Phase 4 adds routing to Brave from the planner, Brave still just works — `search_many` already routes on backend name. The config's `enabled` flag only governs whether Tavily becomes the DEFAULT; explicit planner choices override it.

2. **`_http.py` is the only HTTP helper.** Don't let `hn.py` / `stackexchange.py` reinvent urllib plumbing. They use `http_json(url, ...)`. If either needs auth, pass via `headers={...}` like Brave does.

3. **Sensitive-content filter lives in two places now.** `tokenpal/actions/research/fetch_url.py:75` runs it on local fetch output; `tokenpal/brain/research.py:_read` runs it on preloaded content (Tavily). Any new preloaded-content backend must also run the filter in `_read` — the ABC doesn't enforce this because `preloaded_content` is a data field, not a method.

4. **Thin-pool top-up is Tavily-specific.** `_search_all` only top-ups when `_cloud_search_active` is true. If Phase 4 smart-planner ends up routing to Brave/HN/SE and one returns empty, the top-up logic won't fire. Decide in Phase 4 whether to generalize or keep Tavily-specific.

5. **Legacy `/cloud` flat subcommands still route correctly.** `app.py:_handle_cloud_command` routes `enable/disable/forget/model/plan/deep/search` through to `_handle_cloud_anthropic`. Don't remove these shims — they match existing muscle memory + docs.

6. **Per-query backend field already flows end-to-end.**
   - `_parse_planner_output` picks up `"backend"` from the planner JSON (liberal — ignores unknown)
   - `PlannedQuery.backend` carries it
   - `_resolve_backend(planned)` normalizes + falls back if unknown
   - `_search_all` dispatches per-query

   So Phase 4's planner-prompt change is the ONLY code change needed to activate routing. The pipeline is ready.

## Open follow-ups not in this plan

- `/cloud anthropic|tavily|brave` modal UI picker (`tokenpal/ui/cloud_modal.py`) is still single-backend. CLI path works; modal is deferred.
- `[research] telemetry` toggle if phase 5 adds the end-of-run log and users want to opt out.
- Wayback Machine fallback (plan's parking lot) — file as a separate issue after Phase 5 ships with real telemetry.
- Playwright / JS-SPA retry — deferred, gate on post-ship SPA-miss rate from the new telemetry.

## How to resume

```
git log --oneline -10                  # confirm 18736c5 is HEAD
cat plans/research-source-upgrade.md   # full plan
.venv/bin/python -m pytest tests/ -q   # baseline: 1244 passing
```

Then: `/plan research-source-upgrade` (resume mode) → continue at Phase 3.

First code move in the new session: create `tokenpal/senses/web_search/hn.py` using `_http.py` and the pattern already in `tokenpal/senses/world_awareness/hn_client.py`.
