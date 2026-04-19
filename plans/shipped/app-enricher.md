# App Enricher — let the buddy learn unfamiliar apps

**Status:** proposed — approval pending
**Why now:** Finn riffed on Cronometer from training-data knowledge alone
(impressive but brittle). New apps, region-specific apps, and anything
post-training-cutoff are invisible. A tiny search + cache closes the gap
without rewriting the observation prompt.

## Shape

On first sighting of a new app name (from `app_awareness`), **block
the tick** while `search_web("<app_name> software")` runs (3s timeout),
store the first sentence in `memory.db`, and inject it into the context
snapshot as `App: Cronometer (nutrition and calorie tracking app)`. The
observation LLM stays unchanged — it just gets smarter input.

**Why sync not async**: avoids a context-less quip on the first tick
for a new app. Cost: ~1-2s added latency on first sighting only —
subsequent ticks for the same app are instant cache hits. Cache is
persistent (30d) so the latency hit is per-app-ever, not per-session.

## Data

New table in `memory.db`:
```sql
CREATE TABLE app_enrichment (
    app_name TEXT PRIMARY KEY,
    description TEXT,           -- one sentence; NULL on failure
    fetched_at REAL NOT NULL,
    success INTEGER NOT NULL    -- 0/1
);
```

Migration: additive, handled by `MemoryStore.ensure_schema`.

## Module

`tokenpal/brain/app_enricher.py`

```python
class AppEnricher:
    REFRESH_AFTER_DAYS = 30
    RETRY_AFTER_HOURS = 24
    FETCH_TIMEOUT_S = 3.0

    def __init__(self, memory: MemoryStore, search: SearchClient,
                 sensitive_apps: set[str]) -> None: ...

    async def enrich(self, app_name: str) -> str | None:
        """Cached description if fresh; else blocks on search_web up to
        FETCH_TIMEOUT_S and caches the result. Returns None on timeout,
        consent denial, sensitive app, non-app filter, or empty result."""
```

Async signature so callers await it. Caller (snapshot builder) runs in
the brain-loop async context already, so blocking here pauses only the
observation tick — senses keep polling.

In-flight dedup: a `dict[str, asyncio.Task]` so if two ticks race on
the same new app they share a single fetch.

## Wiring

- `MemoryStore.get_app_enrichment(name)` / `.put_app_enrichment(name, desc, success)` — 20 LOC.
- `ContextWindowBuilder` (or `orchestrator._build_snapshot`): when the
  active reading's sense is `app_awareness`, look up enrichment and
  append `(description)` to the app name in the snapshot.
- `Brain.__init__`: construct `AppEnricher`; pass `memory`, `search_client`,
  `SENSITIVE_APPS`.
- First-sighting detection: track seen-this-session in a `set[str]` on
  the enricher so we don't hammer `get_app_enrichment` every tick.

## Gating

1. `web_fetches` consent must be granted (shared gate with `/ask`).
2. App name not in `SENSITIVE_APPS` — banking/messaging/password apps
   never get searched. Their names also never persist to the cache.
3. App name passes `contains_sensitive_term` — belt-and-suspenders.
4. Not an obvious non-app (filter list: `Finder`, `loginwindow`,
   `WindowServer`, `SystemUIServer`, `Dock`, `ControlCenter`, etc.) —
   these aren't interesting to enrich.

## Failure modes

- Search returns empty → cache `success=0`, retry after 24h.
- Search returns result but sensitive-term filter strips it → same.
- Timeout (3s) → treat as empty, cache `success=0` with 24h backoff so
  a flaky network doesn't retrigger every tick.
- Description longer than one sentence → trim at first `.`/`?`/`!`,
  max 120 chars.
- Cache row > 30 days old → treated as miss, re-fetch.

## Tests

`tests/test_brain/test_app_enricher.py`:
- `test_first_sighting_returns_none_and_schedules_fetch`
- `test_cached_description_returned_synchronously`
- `test_sensitive_app_never_enriched`
- `test_non_app_filter_skips_window_server`
- `test_no_consent_caches_nothing`
- `test_stale_cache_triggers_refetch`
- `test_fetch_failure_backs_off_24h`
- `test_description_trimmed_to_one_sentence`

Plus one integration test in `test_orchestrator_idle_path.py` confirming
that a snapshot with a cached app gets the description appended.

## Out of scope (for this plan)

- Window-title enrichment (titles change fast; signal/noise bad).
- Proactive bulk enrichment of historical apps from `observations` table.
- Enriching non-app context (URLs, file names). Different problem.

## File-level changes

### NEW
- `tokenpal/brain/app_enricher.py` — the module above.
- `tests/test_brain/test_app_enricher.py` — test matrix.

### EDIT
- `tokenpal/brain/memory_store.py` — schema + two accessor methods.
- `tokenpal/brain/orchestrator.py` — construct enricher, inject into
  snapshot path.
- `tokenpal/brain/context.py` — OR snapshot builder, wherever the
  `App:` line is formatted. (Check before coding.)
- `CLAUDE.md` — one-line pointer under Senses or Brain.

## Estimated size

~150 LOC prod, ~200 LOC test. Single session.

---

# Companion: observation max_tokens bump

Separate from enrichment, the user's GPU is idling under the 60-token
cap. Log shows ~57 t/s on Qwen3-14B-Q4 on apollyon:

- 60 tokens = ~1s  (current)
- 150 tokens = ~2.6s  (proposed)
- 256 tokens = ~4.5s  (/ask territory)

Two-line fix, no new infrastructure:

- Bump `config.default.toml` global `max_tokens` from 60 → 150.
- User can still pin higher via `[llm.per_server_max_tokens]`.

Not in scope here: **proper dynamic scaling** (probe llamacpp `/props`,
measure t/s via warm-up generate, size budgets by target latency per
path: observation=2s, freeform=3s, /ask=10s). That's a follow-up
issue — file it but don't ship in this plan.
