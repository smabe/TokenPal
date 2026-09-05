# Proactive nudges — one reminder tool, persisted, in the buddy's voice

## Phase map
**Phase p1 — schedule model + `reminders` table** — NEXT
- Enters when: start here
- Done signal: `Schedule` round-trips through `memory.db` across a simulated restart; DST spring-forward and fall-back cases have pinned, asserted answers; `PRAGMA user_version` reaches 5 on an existing v4 file with its rows intact
- If it fails: no gate — fix-forward
- Shard: `plans/proactive-nudges-p1.md`

**Phase p2 — scheduler owns the schedule, on a wall clock**
- Enters when: p1 shipped
- Done signal: a reminder armed before a simulated 5-hour gap fires exactly once on resume, not five times; `bedtime_wind_down` runs off a real time-of-day schedule with no `""`-means-skip trick; `one_shot` and `registered_names` are gone
- If it fails: no gate — fix-forward
- Shard: `plans/proactive-nudges-p2.md`

**Phase p3 — one `reminder` tool replacing the four**
- Enters when: p2 shipped
- Done signal: `reminder` arms, disarms and lists from chat on this Mac; the four old names are gone from the registry and the catalog; the tool is absent from `_build_ambient_specs`; a sensitive label is refused
- If it fails: no gate — fix-forward
- Shard: `plans/proactive-nudges-p3.md`

**Phase p4 — nudge emission funnel (canned text only)**
- Enters when: p3 shipped
- Done signal: a fired nudge is spoken on this Mac with `[audio] speak_ambient_enabled = true` — the gap that makes reminders silent today — and still fires during a forced-silence window that would suppress an ambient comment
- If it fails: no gate — fix-forward
- Shard: `plans/proactive-nudges-p4.md`

**Phase p5 — LLM voice, off-loop, plus the docs**
- Enters when: p4 shipped
- Done signal: nudge text differs between two fires of the same reminder; with the inference server stopped a due nudge still fires the canned label and `_run_loop` keeps ticking; `docs/claude/brain.md` gains its first ProactiveScheduler entry
- If it fails: no gate — fix-forward
- Shard: `plans/proactive-nudges-p5.md`

## Status & cold-start
**Approval: APPROVED 2026-09-04**
**Authored at: 3bbe847**
Research 2026-09-04: one primary investigator (surface, seams, recorded intent), one persistence specialist (memory.db schema/migration/privacy/read-back), one concurrency specialist (`_run_loop` map, LLM latency protections, clock semantics, delivery contention). Clock and httpx behaviour probed live on this Mac; sqlite migration and column-add behaviour probed with `.venv/bin/python`.

Operator decisions 2026-09-04, all eight taken before drafting:
1. Rebuild the whole proactive layer, not just collapse the four classes.
2. Armed state persists in `memory.db`.
3. Nudge text is LLM-generated in the buddy's voice, with the canned line as fallback.
4. Schedules cover fixed intervals **and** times of day.
5. Nudges get their **own emission funnel** that shares `filter_response` and TTS, but is **exempt from the ambient comment rate cap** — an armed reminder is a promise, ambient chatter is not.
   **Refined after the audit, and surfaced for sign-off:** the operator's answer named near-duplicate suppression among the shared guards. Evidence says it must not be: `_recent_outputs` is a single shared deque (`orchestrator.py:195,394`), so an unrelated ambient comment can suppress a nudge, and two fires of the *same* reminder generate near-identical text that trips `_NEAR_DUPLICATE_JACCARD = 0.70` (`:187`) — silencing every fire after the first. A recurring reminder repeating itself **is the feature**. So nudges do not dedupe and do not append to `_recent_outputs`.
6. Nudge text is generated **off-loop in a background task**; the tick never awaits the model.
7. The unprompted ambient observation LLM **may not** arm or disarm reminders.
8. Intervals mean **wall-clock** time; anything missed while the machine was closed fires **once** on wake, never replayed.
9. A reminder label is filtered with the **narrow** `contains_sensitive_content_term`, not the broad app list. The broad list refuses `"take a health break"`, `"stay calm"` and `"check messages"`; the narrow one allows them and still refuses `1password`, `venmo` and `whatsapp` (probed). Banks (`chase`, `fidelity`) are allowed as an accepted consequence — the user typed the label themselves.
10. `bedtime_wind_down` becomes a single daily fire. Its current T-60 re-nudge window is dropped: "repeated nagging isn't the point of the wind down".
11. **Issue #36 stays separate; p2 is unchanged (operator, 2026-09-04).** #36 "Scheduled reminders" wants one-shot `fire_at` reminders with NL time-phrase parsing and its own table — one schedule kind away from this plan's interval + daily. Asked at the p1 review gate whether to fold it in as a third `kind`; the operator's answer is no. `Schedule` stays two-kind, the tool exposes no one-shot argument, and #36 is not this plan's to resolve. Do not re-open this in a later phase.

Audit 2026-09-04, three auditors (grounding, executability, coherence). Grounding: 14 wrong claims + 8 off-by-a-line, all fixed; every probe reproduced independently. Executability: all five priority gaps fixed — `list_reminders`'s return type pinned to `list[dict]` (the store sets no `row_factory`, so the house tuple style was incompatible with `Schedule.from_row`), the persistence owner pinned onto `ProactiveScheduler(memory=...)` with `tick()` keeping its list return, the ambient-exclusion mechanism locked to a name check, and three unsatisfiable greps/commands corrected. Coherence: two plan-defeating defects fixed, below.

**Two defects the audit caught that would have defeated the plan:**
1. `_ReminderBase.teardown` (`reminders.py:95-97`) cancels its nudge, and `Brain._teardown_components` awaits `teardown()` on every action at shutdown (`orchestrator.py:2869-2871`). With p3's "cancel unpersists" rule, **quitting the app would have deleted every armed reminder** — the exact outcome this plan exists to deliver. p2 deletes the method; p3 forbids `ReminderAction` from defining one; both phases carry a Done criterion.
2. `filter_response` drops anything under 15 characters (`personality.py:1132`, applied twice). Since the label *is* the canned fallback, a reminder armed as `"stand up"` would have been silently swallowed on **every** fire. p4 locks the fallback to bypass the filter; only generated text is filtered.

Also corrected: this plan does **not** overturn `CONTEXT.md:37-38` (the not-a-Wedge ruling, which survives intact) — it falsifies `CONTEXT.md:108-112`, a passage the first draft never cited. And p4 was split: the funnel and TTS (cheap, certain, live-checkable with no model) now ship separately from the off-loop LLM generation, which carries all the concurrency risk.

Also open: `plans/find-files-open-path.md` is APPROVED with **p4 outstanding** (the Windows Search backend, written on the Mac and unverified on Windows). p1-p3 of it shipped as `f0ca2f0`, `d42522f`, `3bbe847`. The operator moved to this plan first; work them sequentially, not together.

NEXT: p1 — read `plans/proactive-nudges-p1.md` FIRST.

## Goal
Replace four near-identical reminder actions and an in-memory scheduler with one `reminder` tool whose armed state survives a restart, whose schedule covers intervals and times of day without the `""`-means-skip trick, and whose nudge text is generated in the buddy's voice without ever blocking the brain loop.

## Scope contract
- **Requested outcome:** one parameterised reminder tool covering arm / disarm / list; armed state persisted in `memory.db` and reloaded at start; nudge text LLM-generated with a canned fallback; schedules covering fixed intervals and times of day.
- **Named semantic boundary:** the proactive nudge subsystem — `tokenpal/brain/proactive.py`, `tokenpal/actions/focus/reminders.py`, their registration and catalog entries, and the brain-loop tick that drives them.
- **Explicit inclusions:** the `Schedule` representation and its persistence; the scheduler rewrite onto a wall clock; the single tool and its catalog entry; exclusion from the ambient tool set; the nudge emission funnel; the docs that describe all of it.
- **Explicit exclusions:** OS-level notifications (nudges stay speech bubbles — `plans/shipped/pal-improvement-grand-plan.md:240`); a full cron grammar (weekdays, windows, quiet hours); `PomodoroAction`, which uses `asyncio.sleep` rather than the scheduler; the idle-tool, agent, research and desktop-content paths; the unrelated `find-files-open-path` p4.
- **Intent class:** bounded outcome.

## Non-goals
- No change to the pause gates' membership. `_proactive_paused` (`orchestrator.py:887-897`) already covers `_paused`, `_any_long_task`, `_in_conversation` and the sensitive app; the gaps research found (desktop-content runs, voice-conversation state, an open confirm modal) are recorded in the Parking lot, not fixed here.
- No `request_timeout_s` config field for the LLM backend. The 60s HTTP inactivity default (`llm/http_backend.py:42`, unreachable from `LLMConfig`) is a real gap, but decision 6 routes generation off-loop so it stops being this plan's problem.
- No migration of a user's existing `[tools] enabled_tools` entries. A stale name is silently ignored (`registry.py:53-72` iterates the registry, not the allowlist) and the next `/tools` save drops it. p3 states this in its release note rather than writing a config rewriter; this machine is unaffected (`config.toml:41-63` lists none of the four, and `~/.tokenpal/config.toml` does not exist).
- No revival of the `action_configs` injection route. `resolve_actions` accepts it (`registry.py:39`) but neither `app.py:227-231` nor `cli.py:155-159` passes it, so `config.get("scheduler")` and `config.get("message_fn")` are dead; the live route is `_inject_brain_deps` attribute-poking (`orchestrator.py:852-885`), which already handles `_llm` (`:865-866`) and `_memory` (`:863-864`).

## Files touched
- `tokenpal/brain/schedule.py` — p1 (new) — the `Schedule` value type, its wall-clock `next_due_at`, and its serialisation
- `tokenpal/brain/memory.py` — p1 — migration 5 adding `reminders`, plus the accessors
- `tests/test_brain/test_schedule.py` — p1 (new) — interval and time-of-day maths, DST transitions, round-trip
- `tests/test_brain/test_memory_migrations.py` — p1 — a v4→v5 upgrade case and a reminders round-trip
- `tokenpal/brain/proactive.py` — p2 — scheduler owns a `Schedule` instead of a closure; wall clock; hydrate; `one_shot` and `registered_names` deleted
- `tokenpal/actions/focus/reminders.py` — p2 (adapted to the new scheduler), p3 (deleted, replaced by the single tool)
- `tests/test_actions/test_focus/test_proactive.py` — p2 — rewritten onto the schedule model and the wake-once rule
- `tokenpal/brain/orchestrator.py` — p2 (hydrate at start), p3 (ambient exclusion), p4 (nudge funnel + off-loop generation)
- `tokenpal/actions/reminder.py` — p3 (new) — the single arm/disarm/list tool
- `tokenpal/actions/focus/__init__.py` — p3 — docstring no longer names four reminders
- `tokenpal/actions/catalog.py` — p3 — four `FOCUS_SECTION` rows become one
- `tests/test_actions/test_catalog.py` — p3 — pinned name set
- `tests/test_actions/test_focus/test_reminders.py` — p3 — deleted with the actions it tests
- `tests/test_actions/test_focus/test_brain_injection.py` — p3 — retargeted off `StretchReminderAction`
- `tests/test_actions/test_reminder.py` — p3 (new) — arm/disarm/list, sensitive-label refusal, ambient exclusion
- `tokenpal/brain/personality.py` — p5 — the nudge prompt builder
- `tests/test_brain/test_nudge_emission.py` — p4 (new) — funnel guards and TTS; p5 — off-loop generation, timeout and fallback
- `docs/claude/brain.md` — p5 — the ProactiveScheduler entry that has never existed
- `CONTEXT.md` — p5 — `:108-112`'s "never goes through the riff pipeline" is now false; `:37-38` is untouched and still true
- `CLAUDE.md` — p5 — one Privacy line for the new `reminders` table
- `docs/agents-and-tools.md` — p3 — the Focus row is the only file naming all four; it goes false the moment they are deleted

## Background findings
- **The closure is why persistence is impossible today.** `ScheduledNudge.message_fn` is a `Callable[[], str]` (`proactive.py:26,34`), so every schedule variation is smuggled inside it. `bedtime_wind_down` encodes a 60-minute pre-target window as a message function returning `""` outside it (`reminders.py:225-255`), and the scheduler learns about times of day only through the accident of `""` meaning "not now" (`proactive.py:129-132`). The seam is in the wrong place: the scheduler should own the schedule and ask for text only once it has decided to fire.
- **`time.monotonic()` excludes sleep on macOS — measured, not assumed.** `time.get_clock_info('monotonic')` reports `mach_absolute_time()`; on this Mac it read 1126365.15 against `CLOCK_MONOTONIC`'s 1136931.88, i.e. 10,566 s ≈ 2 h 56 m of accumulated sleep over 13 days, cross-checked against `kern.boottime`. `hasattr(time, 'CLOCK_BOOTTIME')` is `False` on Darwin. `asyncio` inherits it (`loop.time()` agrees to 8e-8 s). Windows and Linux behaviour is **[unverified]** — probed on macOS only.
- **A fire-time LLM call would inherit no protection.** `target_latency_s` is a max-tokens sizing hint, not a timeout (`llm/http_backend.py:203-247`); nothing aborts an overrun. No `asyncio.wait_for` bounds any LLM `generate()` call. One does exist on an inline-drained path — `_new_conversation_session` (`orchestrator.py:978`) bounds a wait on a pending summary task with `shield` and a `TimeoutError` fallback — and that is the precedent p4 follows, not an absence. `request_timeout_s` is read from `backend_config` (`http_backend.py:42`) and `LLMConfig` has no such field (`config/schema.py:107-131`), so nothing in TOML can set it; it is reachable only at a call site via `backend_config(**overrides)` (`llm/registry.py:42-59`), which `tools/train_voice.py:78` uses. The brain's backend is built once at `app.py:198`, so no per-call ceiling is available to this subsystem from config either way — and httpx's timeout is per-read, not a total-call cap (probed: a trickling server raised `ReadTimeout` at the per-read bound, so a proxy could hold a call past 60 s). `_consecutive_failures` is incremented only inside `_generate_comment` (`orchestrator.py:1598`), so a scheduler-tick call participates in no breaker.
- **The LLM client is concurrency-safe.** One `httpx.AsyncClient` (`http_backend.py:136`), no lock/semaphore/queue anywhere in `tokenpal/llm/` (grepped `_lock|Semaphore|circuit|breaker`). Probed: three concurrent POSTs ran in parallel. The repo already issues LLM calls off the loop — `SessionSummarizer.run_forever` (`orchestrator.py:666`), the EOD bubble (`:594`), the conversation summary (`:962`). Two caveats: `HttpBackend`'s EWMA estimator state is shared mutable (`http_backend.py:67-71`), so overlapping calls mis-attribute each other's queueing delay to TTFT; and the real serialisation point is the inference server, so an off-loop nudge generation queues behind an in-flight summariser call.
- **Nudges bypass every guard the bubble paths use.** `ProactiveScheduler` calls the raw `ui_callback` (`proactive.py:133`, wired at `orchestrator.py:331` to `app.py:319`), never `_emit_comment` (`orchestrator.py:1136-1153`). So no `record_comment`, no `_recent_outputs` append (invisible to `_is_near_duplicate`, `:1241-1264`), no rate accounting, no `_context.acknowledge()`, no `filter_response`, and **no TTS** — `_speak_async(text, source="ambient")` is called only from `_emit_comment` (`:1147`), verified: its only other call site is the typed-reply path (`:2610`). Nudges are silent today even with `speak_ambient_enabled = true`.
- **The repo's one working answer to "an event we owed you while closed" is fire-once, date-keyed.** `has_shown_eod`/`mark_eod_shown` (`memory.py:589,602`) key on `today_str()`/`yesterday_str()` (`eod_summary.py:42-47`), consumed at `orchestrator.py:591-610`. Read-once-at-start precedents that never replay: `_load_previous_session_note` (`orchestrator.py:628-645`), the recap built inline in `_new_conversation_session` (`orchestrator.py:971-1010`, `build_conversation_recap(text, age_s)` at `:1006`) — it passes the gap's age into the prompt so the buddy acknowledges it.
- **Migrations are append-only and cheap; column adds are not.** `_MIGRATIONS` is a 4-entry list and `CURRENT_SCHEMA_VERSION = len(_MIGRATIONS)` (`memory.py:177-184`, verified), applied via `PRAGMA user_version` (`:274-291`). Probed: re-running `CREATE TABLE IF NOT EXISTS` with a new column against an existing table is a **silent no-op** and the first insert naming that column raises `OperationalError`. Probed: `executescript` DDL and the version bump run in autocommit, so a migration raising mid-body leaves partial tables durable with the version un-bumped — every shipped migration is `IF NOT EXISTS` throughout to survive the re-run, and a new one must preserve that.
- **`_prune()` deletes from `observations` and `conversation_summaries` older than `retention_days`** (default 30, `memory.py:804-820`, `schema.py:200-202`). Reminders must live in their own table or a month-old armed reminder is deleted at startup.
- **The reject-not-redact shape comes from `active_intent`; the list does not.** `intent.py:79` applies the broad `contains_sensitive_term` (`personality.py:285`, all of `SENSITIVE_APPS`) to user-authored intent text and refuses. This plan keeps the refusal shape and **departs on the list** (decision 9): the broad list carries eleven ordinary English words and would refuse ordinary self-care labels. The narrow `contains_sensitive_content_term` (`personality.py:293`) is nominally for untrusted *network* text (`fetch_url.py:75`, `research.py:634`), but the property that matters here is the one its docstring describes — it drops every term that is also a common English word — and it is a deliberate carve-out, not an oversight, that it therefore misses two banks.
- **`MemoryStore` is blocking and shares a lock with the UI thread.** One connection, `check_same_thread=False`, WAL, no `timeout=` override (`memory.py:268-272`); `threading.Lock` around statement groups (`:200`); `record_chat_entry` fires on the Qt thread (`app.py:1789-1795`) while the brain loop calls it too. Existing TTL caches (`memory.py:207-209`) exist precisely because `_build_idle_context` runs every 2-3 s — a per-tick reminders query would sit on the same hot path.
- **Dead state, verified.** `one_shot` (`proactive.py:40,74,83,136`) has no production caller — `BedtimeWindDownAction` does not pass it, and the only `one_shot=True` in the tree is `tests/test_actions/test_focus/test_proactive.py:86`. Its comment (`proactive.py:36-39`) is doubly wrong: bedtime rolls its target forward a day (`reminders.py:248-249`), so it repeats nightly forever — the opposite of one-shot. `registered_names()` (`proactive.py:97`) has **zero callers anywhere**, verified by grep over `tokenpal/` and `tests/`.
- **`_proactive_paused` is broader than the scheduler's own docstring claims.** `proactive.py:51-53` says "conversation.is_active OR sensitive_app_in_foreground"; the code (`orchestrator.py:887-897`) also covers `_paused` and `_any_long_task`. It also rebuilds `self._context.snapshot()` (`:895`) although `_run_loop` already built one at `:726`.
- **Recorded intent this plan contradicts, deliberately.** `plans/shipped/pal-improvement-grand-plan.md:106` justified four separate checkboxes "so users can enable pomodoro without also signing up for water reminders". One tool moves that granularity from the picker to runtime arm/disarm. `plans/shipped/wedge-unification.md:50-51` rules the scheduler out of Wedge unification as "a multi-tenant scheduler with its own tick semantics" — a scope non-goal of a shipped plan, not a durable ruling, and this plan rewrites it. `CONTEXT.md:37-38` ("a multi-tenant scheduler that self-emits on its own clock") **still holds in full** and is not edited. The passage that this plan actually falsifies is `CONTEXT.md:108-112`: "it self-emits through `ui_callback` and **never goes through the riff pipeline**" — p4 routes nudges through `filter_response` and TTS, which is most of that pipeline. That is the line to rewrite.
- **Two brainstorms already scoped this.** `plans/brainstorm/senses-tools-privacy.md:53-55` proposes `/remind <time> <text>`, "SQLite-backed, no network", GREEN LIGHT. `plans/brainstorm/senses-tools-ml.md:61` warns: "better to do rule-based date parsing in code, have LLM just fill slots" — which is the division of labour p3 adopts.

## Done criteria
- On this Mac: arm a reminder in chat, quit, relaunch, and it is still armed and still fires — the state survives a restart, which nothing in this subsystem has ever done.
- A reminder that came due while the app was closed produces exactly one bubble on relaunch, not one per missed interval.
- A fired nudge is spoken aloud when `[audio] speak_ambient_enabled` is true, and its text differs run to run rather than repeating a constant.
- With the inference server stopped, a due nudge still fires the canned line, and `_run_loop` keeps ticking throughout — sense polling and chat replies stay responsive.
- The four old tool names are absent from `resolve_actions`, the catalog and `/tools`; `reminder` appears once; the model cannot arm one from an unprompted ambient tick.
- `pytest` green, `ruff check tokenpal/` clean, `mypy tokenpal/ --ignore-missing-imports` clean.

## Parking lot
- ADJACENT: `_proactive_paused` ignores an in-progress desktop-content run (`_handle_desktop_task` sets no `_mode`, `orchestrator.py:1966-2014`), voice-conversation state (`audio/session.py:29-120`), and an open confirm modal (the Qt dialog is `show()`-based, so the loop keeps pumping). A nudge can fire mid-utterance with the mic open.
- ADJACENT: `HttpBackend`'s EWMA estimator state is shared mutable across concurrent generations, biasing the TTFT figure that every path's max-tokens sizing depends on.
- ADJACENT: `LLMConfig` has no `request_timeout_s`, so the 60 s HTTP default is unreachable from config; httpx's timeout is per-read, not a total-call cap.
- ADJACENT: the `action_configs` injection route is dead in production; `pomodoro.py:66`'s comment claims otherwise.
- ADJACENT: `PomodoroAction` uses `asyncio.sleep(work_min * 60)` (`focus/pomodoro.py:110-112`), which stops during system sleep — a pomodoro started before a lid-close resumes mid-cycle.
- ADJACENT: nothing anywhere in the repo is sleep/wake aware; 114 `time.monotonic()` call sites, none gap-aware.
- ADJACENT: `README.md` (`:214`) says "focus (pomodoro, water/stretch reminders) tools". After p3 a reminder tool still exists, so the sentence does not go false — reword it whenever README is next touched.
- ADJACENT: `ProactiveScheduler.is_registered()` (`proactive.py:94`) is orphaned by p2's rewrite (its only callers are the two test files p2 rewrites and p3 deletes). p2 deletes it alongside `one_shot` and `registered_names`; noted here so the count of removed dead members is three, not two.
- ADJACENT: `active_intent` has the same false-positive problem this plan fixes for reminders — `intent.py:79` applies the broad `contains_sensitive_term` to user-authored intent text, so `"fix the health dashboard"` is refused. Same shape, different feature; not this plan's to change.
