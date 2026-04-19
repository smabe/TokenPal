# Buddy utility wedges

## Goal
Move TokenPal from pure companion toward companion + executive-function by shipping five bounded utility features that reuse existing senses/memory: session handoff, intent tracking, end-of-day summary, rage/frustration detect, and proactive git nudges. Anchor feature is session handoff — periodic LLM summaries every ~5min, read back on startup.

## Non-goals
- No new senses. Every wedge builds on existing sense output (app_awareness, git, idle, typing_cadence, process_heat, MemoryStore).
- No semantic memory / vector DB work. Summaries are plain text in SQLite; semantic retrieval stays parked.
- No cross-machine sync. Summaries and intents live in local `memory.db`.
- No notification surface outside the existing speech bubble + chat log. No OS notifications, no email, no Slack.
- No calendar/meeting integration (EventKit plan stays parked).
- No productivity dashboards or web UI. EOD summary is delivered as a buddy bubble, not a page.
- No intent NLP: `/intent` is literal free-text the user types; we don't try to infer intent from activity.

## Files to touch

### Phase 1 — Session handoff (anchor)
- `tokenpal/brain/session_summarizer.py` (NEW) — periodic summarizer. Skip-if-idle guard, prompt builder, writer. Scheduled via `asyncio.create_task()` in `orchestrator._run_loop()` — not a sense subclass.
- `tokenpal/brain/memory.py` — new `session_summaries` table, `record_summary()`, `get_recent_summaries(since_ts, limit)`, `get_latest_summary()`. Introduce `PRAGMA user_version` + migration-list scaffolding in this phase (closes parking-lot #31 G2 as side effect; supersedes the per-table column-level pattern going forward).
- `tokenpal/brain/orchestrator.py` — boot hook in `start()` after sense setup to read latest summary; spawn 5min summarizer task in `_run_loop()`.
- `tokenpal/brain/personality.py` — new `{previous_session}` slot in `_PERSONA_TEMPLATE` (and fine-tuned observe variant); `build_prompt()` gains `previous_session: str | None = None` kwarg, empty string when absent.
- `tokenpal/config/schema.py` — `SessionSummaryConfig(enabled: bool, interval_s: int = 300, max_lookback_h: int = 24)`.
- `config.default.toml` — `[session_summary]` block, default enabled=true.
- `tests/test_brain/test_session_summarizer.py` (NEW) — skip-if-idle guard, sensitive-term drop, startup read.
- `tests/test_brain/test_memory_migrations.py` (NEW) — PRAGMA user_version bumps, upgrade path from version 0 db applies all migrations cleanly.

### Phase 2 — Intent tracking
- `tokenpal/brain/intent.py` (NEW) — `IntentStore` facade: set/get current intent, timestamp, drift check.
- `tokenpal/brain/memory.py` — `active_intent(text, started_at, session_id)` table; single row replaces prior.
- `tokenpal/app.py` — `/intent <free text>`, `/intent clear`, `/intent status` slash commands.
- `tokenpal/brain/orchestrator.py` — drift-check ticker (every 60s): compare current app + typing state to intent, emit a drift nudge when conditions trip.
- `tokenpal/brain/personality.py` — drift nudge prompt variant, shares pacing gate with observations.
- `tests/test_brain/test_intent.py` (NEW) — set/clear round-trip, drift trigger conditions, pacing gate.

### Phase 3 — End-of-day summary
- `tokenpal/brain/eod_summary.py` (NEW) — 24h rollup from MemoryStore + session_summaries; builds a buddy-voice bubble.
- `tokenpal/brain/orchestrator.py` — fires on first interaction after a local-midnight rollover, or on `/summary` command.
- `tokenpal/app.py` — `/summary [today|yesterday]` slash command.
- `tests/test_brain/test_eod_summary.py` (NEW) — rollup math, midnight rollover detection, empty-day handling.

### Phase 4 — Rage / frustration detect
- `tokenpal/brain/rage_detector.py` (NEW) — detects frustration pattern from existing signals only: sustained typing burst (`typing_cadence` bucket = `rapid`/`furious`) → ≥60s pause → app switch to known distraction (Twitter/Slack/Reddit/YouTube) within 30s. No keyboard-bus changes; no key values ever observed.
- `tokenpal/brain/orchestrator.py` — subscribe detector, emit single in-character check-in ("stuck?" variant) via `changed_from` high-signal bypass path, 10min cooldown per session.
- `tokenpal/brain/personality.py` — rage-check prompt variant, opt-outable.
- `tokenpal/config/schema.py` — `RageDetectConfig(enabled: bool = false, distraction_apps: list[str])`.
- `tests/test_brain/test_rage_detector.py` (NEW) — pattern match, cooldown, opt-out.

### Phase 5 — Proactive git nudges
- `tokenpal/senses/git/__init__.py` — expose `last_commit_ts`, `uncommitted_lines` (already tracked) via `SenseReading.data`.
- `tokenpal/brain/git_nudge.py` (NEW) — rule: WIP for >3h, last commit msg matches `WIP|wip|tmp|todo`, not currently idle. Emits at most once per 2h.
- `tokenpal/brain/orchestrator.py` — subscribe, share pacing gate.
- `tokenpal/brain/personality.py` — git-nudge prompt variant.
- `tests/test_brain/test_git_nudge.py` (NEW) — window logic, WIP-pattern match, cooldown.

## Failure modes to anticipate

### Session handoff
- **LLM call on 5min timer starves the brain loop.** Observation + conversation calls share the same backend. Summarizer must yield / use same `target_latency_s` scaling, and must run in the async brain loop, not the UI thread.
- **Summarizer fires during sensitive-app window.** Build window excludes those ticks; additionally, LLM output passes through `contains_sensitive_term` before insert (drop whole row, no partial redaction).
- **Clock skew / laptop sleep.** 5min wall-clock ticker sees huge gap on resume. Treat resume as a synthetic "session boundary" — emit one summary for the pre-sleep window, reset cadence.
- **Summary prompt gets too long** as MemoryStore fills up. Cap input to last 5min of activity + current active intent. Don't include prior summaries in the prompt (they already compressed it).
- **Startup read pulls stale summaries from months-old sessions.** `max_lookback_h = 24` config cap; query must bound by timestamp, not just `ORDER BY ts DESC LIMIT N`.
- **Crash mid-summary.** Summary is one INSERT after LLM returns. Partial summary never persists. Acceptable data loss = last 5min.

### Intent tracking
- **User sets an intent and never clears it.** Drift checks fire forever. Auto-expire intents after 8h silence, or on new session start.
- **Drift check false positives.** "Drift" is easy to get wrong — reading docs in a browser might be on-task. Bias toward fewer nudges: require both app-switch-to-distraction AND ≥5min in the new app. Pacing gate still applies.
- **Intent text leaks through `contains_sensitive_term`.** Filter intent text on set; reject with polite message if it matches.

### EOD summary
- **First-ever run has no prior day to summarize.** Empty-day path: no bubble, silent no-op.
- **"Midnight" ambiguity across timezones / DST.** Use local `datetime.now()` date boundary, recomputed each check. Accept one-off weirdness on DST days.
- **24h rollup gets huge on a heavy day.** Aggregate to app-minute counters + commit count before prompting LLM; never dump raw rows.

### Rage detect
- **Typing-burst false positives.** Normal flow work can look like a rapid-typing run; not every burst-then-pause-then-app-switch is frustration. Opt-in only (`enabled = false` default), user configures distraction_apps list. Wait for at least 60s post-burst pause before arming the switch-watch to cut chatty false positives.
- **Zero keyboard-bus changes this phase.** Uses only `typing_cadence` bucket transitions (already hysteresis-guarded) + `app_awareness` switch events. No key values ever observed by the detector; no new privacy surface.
- **Annoyance budget.** Wrong nudge is worse than no nudge. 10min cooldown per session + in-character wording ("still in it?") not accusatory. Emits via high-signal bypass path since by definition rare.

### Proactive git
- **git sense poll is 15s but WIP detection wants hours.** Track `last_non_wip_commit_ts` separately; don't re-derive from every poll.
- **Commit-message substring match is brittle.** `WIP|wip|tmp|todo` covers typical cases; document that users with "wiper" in commit messages will trip it. Acceptable.
- **Nudge fires while user is actively committing** (race between git poll and the nudge). 2h cooldown + pacing gate absorbs this.

### Cross-cutting
- **Pacing strategy per feature.** Single gate at `orchestrator._should_comment()`; no pass-through mode. Routing:
  - Intent-drift nudge → normal pacing gate (just another candidate observation).
  - Rage nudge + proactive-git nudge → high-signal bypass via `changed_from` on the emitted reading (same pattern git sense uses). Still respects consecutive-same-topic suppression.
  - EOD summary → user-triggered via `/summary` or once-daily on first interaction post-midnight; outside the gate entirely, not spammy by construction.
  - Session summarizer → doesn't emit bubbles; writes silently to `memory.db` and is read at boot.
- **Each feature must be independently disableable.** Config flag per phase, default enabled for session handoff + proactive git, default disabled for rage detect, intent tracking is user-initiated so no flag needed.
- **MemoryStore migrations via PRAGMA user_version.** Phase 1 introduces the scaffolding: `PRAGMA user_version` read at boot, a migration list of `(from_version, apply_fn)` pairs, each phase bumps the version and appends a migration. Supersedes the existing column-level `schema_version` pattern on `llm_throughput_estimators` (leave that column alone for now; don't backfill the bump). Closes parking-lot #31 G2.
- **Privacy layer consistency.** Every text written to `memory.db` from these features must pass `contains_sensitive_term` (the app-list gate at `personality.py:232`). No partial redaction. Do NOT use `contains_sensitive_content_term` — that's reserved for untrusted external web content.

## Done criteria

**Phase 1 (session handoff):**
- `[session_summary] enabled = true` persists summaries every 5min skipping idle windows
- On restart, the latest summary within `max_lookback_h` is visible in the buddy's first observation bubble
- Summary row never persists if `contains_sensitive_term` matches
- Skip-if-idle guard verified via unit test
- `--verbose` shows the summary being embedded in the first prompt

**Phase 2 (intent):**
- `/intent "finish auth PR"` sets, `/intent status` reads, `/intent clear` removes
- Drift nudge fires when user moves to configured distraction app + stays >5min, respects pacing gate
- Intent text passes `contains_sensitive_term` on set
- Auto-expires after 8h

**Phase 3 (EOD summary):**
- `/summary today` produces a bubble with app-time + commit stats
- First interaction after local midnight triggers yesterday's summary once
- Empty day → silent no-op, no bubble

**Phase 4 (rage detect):**
- Default disabled; enabling + hitting the pattern (rapid/furious typing bucket → ≥60s pause → distraction app switch within 30s) produces one check-in bubble
- 10min cooldown enforced
- Detector imports only `typing_cadence` + `app_awareness` readings; does NOT touch `_keyboard_bus`, verified by source-grep test on `rage_detector.py`

**Phase 5 (proactive git):**
- WIP > 3h with matching commit message produces one bubble per 2h window
- Respects idle state (no nudge when user is away)

**Cross-cutting:**
- `memory.db` schema version bumped, migrations run cleanly on upgrade from a pre-feature db
- `tokenpal --validate` reports on all five features
- Per-phase commit
- Run `/simplify` after each phase (per feedback memory)

## Parking lot
