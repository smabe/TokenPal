# Observation Enrichment

When an observation comment is about to fire, the Brain splices
one-line descriptions into the snapshot so the LLM has richer context
to riff on. `App: Cronometer` becomes
`App: Cronometer (nutrition tracker)`; a `process_heat` firing line
gets the hot process's description appended.

Read this before adding a new per-sense enrichment or editing
`tokenpal/brain/observation_enricher.py` / `tokenpal/brain/app_enricher.py`.

## Why it exists

A bare observation like "App: Cronometer" gives the LLM little to
work with beyond the name. With a description spliced in, the buddy
can comment on *what* Cronometer is (a nutrition tracker) without
having to have that knowledge in its fine-tune. Same for processes
that spike the CPU — naming "Docker Desktop" is less useful than
"Docker Desktop is container runtime manager."

## Architecture

Two layers:

1. **`AppEnricher`** (`tokenpal/brain/app_enricher.py`) — owns the
   actual lookup + cache. One method: `async enrich(name) -> str | None`.
   Backed by the `app_enrichment` SQLite table (30d TTL on success, 24h
   retry-backoff on failure), a per-session cache, consent gating,
   sensitive-app / platform-process filtering, and a 3s blocking-fetch
   cap on first encounter. Called from the web-search primitive, not
   an LLM tool.

2. **`ObservationEnricher`** (`tokenpal/brain/observation_enricher.py`) —
   orchestrates per-sense handlers against a snapshot. One method:
   `async enrich(snapshot, readings) -> str`. Dispatches to
   handler methods (`_enrich_app_awareness`, `_enrich_process_heat`,
   …) that either rewrite the snapshot in place or bail.

`Brain._maybe_enrich_snapshot` is a thin dispatcher that calls
`ObservationEnricher.enrich` with the current active readings. It runs
once per observation emission, before prompt composition.

## Flow, per observation

```
┌─ _generate_comment ──────────────────────────────────────────┐
│                                                              │
│  snapshot = context.snapshot()                               │
│  if sensitive_app:     → return False                        │
│  if easter_egg:        → emit verbatim, return True          │
│                                                              │
│  snapshot = await self._maybe_enrich_snapshot(snapshot)      │
│    → ObservationEnricher.enrich(snapshot, readings)          │
│      → _enrich_app_awareness                                 │
│           app_name = readings["app_awareness"].data.app_name │
│           desc = await AppEnricher.enrich(app_name)          │
│           if desc: splice "App: <name> (<desc>)"             │
│      → _enrich_process_heat                                  │
│           proc = readings["process_heat"].data.top_process   │
│           desc = await AppEnricher.enrich(proc)              │
│           if desc: splice "<summary> — <proc> is <desc>"     │
│      → (future: _enrich_git, _enrich_filesystem_pulse, …)    │
│                                                              │
│  prompt = build_prompt(snapshot, …)                          │
│  response = await llm.generate(prompt)                       │
│  filtered = filter_response(response.text)                   │
│  emit or suppress                                            │
└──────────────────────────────────────────────────────────────┘
```

## Handlers today

### `_enrich_app_awareness`

Splices the foreground app's description into the snapshot's
`App: <name>` line.

```
App: Cronometer | It's 10 AM
→
App: Cronometer (nutrition tracker) | It's 10 AM
```

Behavior migrated verbatim from an earlier `Brain._maybe_enrich_snapshot`.
AppEnricher owns consent gating, sensitive-app skipping, the
NON_APP_NAMES filter (Finder, WindowServer, explorer.exe, …), and the
3s blocking-fetch cap on first encounter for an unseen app.

First encounter with an unknown app blocks the observation tick for up
to 3s, caching the result in `memory.db`. Subsequent ticks for the
same app are instant cache hits. Cache is 30 days on success, 24h on
failure.

### `_enrich_process_heat`

When the `process_heat` sense fires (CPU pinned > 80% for 20s), its
`data` carries `top_process`. The enricher looks up the process name
via AppEnricher (same cache table — process names look enough like
app names) and appends the description to the sense's summary.

```
CPU pinned — Docker Desktop is working hard
→
CPU pinned — Docker Desktop is working hard — Docker Desktop is container runtime manager
```

Sensitive-app filtering still applies via AppEnricher's
`contains_sensitive_term` check: a Signal or banking process pinning
the CPU never gets enriched.

## Adding a new handler

1. Write a small coroutine on `ObservationEnricher`:

   ```python
   async def _enrich_<sense_name>(
       self, snapshot: str, readings: dict[str, Any],
   ) -> str:
       reading = readings.get("<sense_name>")
       if reading is None:
           return snapshot
       # cheap gating first — bail on anything that would skip the lookup
       ...
       description = await self._app_enricher.enrich(<key>)
       if not description:
           return snapshot
       return snapshot.replace(<old>, <new>, 1)
   ```

2. Wire it into the chain in `enrich()`:

   ```python
   snapshot = await self._enrich_app_awareness(snapshot, readings)
   snapshot = await self._enrich_process_heat(snapshot, readings)
   snapshot = await self._enrich_<sense_name>(snapshot, readings)   # <- add
   return snapshot
   ```

3. Tests in `tests/test_brain/test_observation_enricher.py`. Use
   `_StubAppEnricher` from that file; the pattern is
   async-mocked, no real network.

**Latency discipline.** Every handler runs on the observation hot path.
Each extra network-backed handler is a potential 3s wait on first
encounter. Stay under this budget:

- Reuse AppEnricher when the lookup target looks like an app/service
  name (same cache table, same gating).
- If a handler needs a different lookup backend, give it its own
  30-day cache table + 3s timeout + retry-backoff. Don't invent a
  new latency posture.
- Prefer local signals when possible (git subprocess, memory_query)
  before falling back to the network.

## Handlers on the parking lot

- **`_enrich_git`** — append commit count today + branch age when the
  git sense reports a fresh commit. Pure local subprocess, no network,
  but needs the repo root plumbed in. Deferred because the git sense
  already carries the useful metadata; the enricher would need a
  dedicated helper to run a second `git` call.
- **`_enrich_new_domain`** — look up an unfamiliar browser domain
  (e.g. "adsmith.io → publisher tech startup"). Deferred because it
  adds a new privacy surface (previously-unseen domain names hitting
  the network) that needs its own consent UX review.

Both track in `plans/shipped/interesting-plus-plus-bcdf.md`'s parking
lot — future work, not forgotten.

## Privacy posture

- **Consent** — AppEnricher short-circuits when `web_fetches` consent
  is missing. The cache still serves previously-known apps, but no
  new lookups.
- **Sensitive apps** — `contains_sensitive_term` (from
  `personality.py`) runs on both the name being looked up AND the
  returned description. Match on either → cache as failure, don't
  splice.
- **Platform noise** — `NON_APP_NAMES` (Finder, WindowServer,
  explorer.exe, svchost.exe, …) short-circuits before the lookup.
- **Content filtering** — `contains_sensitive_content_term` (broader
  than app-name match) runs on the description before caching it.

The same guards auto-apply to new handlers that route through
AppEnricher. If a handler routes through its own cache, port the same
gating checks — don't roll your own.

## Known non-goals

- **LLM-chosen enrichment** — deciding at runtime which fact to splice
  based on the LLM's inclination. The runtime overhead and cringe risk
  outweigh the value; stick with deterministic per-sense handlers.
- **Multi-sentence descriptions** — AppEnricher trims to
  `MAX_DESCRIPTION_CHARS=120` and one sentence. A description that
  sprawls defeats its purpose (the point is a cheap context lift,
  not a paragraph).
- **Cache invalidation on UI** — apps don't change identity often
  enough to justify a refresh trigger. The 30-day TTL handles drift;
  the 24h retry handles transient failures.
