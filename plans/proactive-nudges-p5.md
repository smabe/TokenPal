# proactive-nudges-p5 — LLM voice, off-loop, plus the docs

You are phase `p5`, the last phase of the `proactive-nudges` plan. This phase delivers, as one commit, nudge text generated in the buddy's voice off the brain loop, with the canned label as fallback, and the documentation this subsystem has never had. p4 shipped the emission funnel, which already filters generated text and already speaks; you are supplying the text and the concurrency.

## Locked decisions
See the master `plans/proactive-nudges.md`. Binding here:
- **Generation runs off-loop** (operator decision 6). `tick()` returns the fired nudges (p2) and stays synchronous; the orchestrator spawns one task per fired nudge. The tick never awaits the model.
- **The canned label is the fallback** — on timeout, on exception, and when `filter_response` returns `None`. It bypasses the filter (p4's locked decision), so a short label still speaks.
- **Bounded even though off-loop.** Off-loop removes the *stall*, not the unboundedness. `request_timeout_s` cannot be set from TOML (`LLMConfig` has no such field, `config/schema.py:107-131`) and the brain's backend is built once at `app.py:198`, so no per-call ceiling is available from config; and httpx's timeout is per-read rather than a total-call cap (probed: a server trickling 1 byte/s held a `timeout=2.0` GET for 6.01 s and completed 200).
- **`_NUDGE_TIMEOUT_S = 15.0`**, not "a few seconds". It must sit *above* the latency budget the sizing hint derives from, or the timeout fires every run on a slow first-token machine, the canned label always ships, and the "text differs between fires" criterion silently never holds while every test passes.
- **Reuse the `freeform` budget** — `target_latency_s=self._budgets.freeform` (6.0 s, `config.default.toml:88`) and `min_tokens=self._min_tokens.freeform`. `TargetLatencyConfig` has six named paths and no nudge path (`config/schema.py:96-103`); adding one is out of scope.

## Work
- Scope trace: DIRECT — operator decision 3, the requested outcome's voice half.
- `tokenpal/brain/personality.py` — a nudge prompt builder beside `build_git_nudge_prompt` (`:1046`, measured at `58195eb`), the closest precedent: it branches on `is_finetuned` and interpolates `_identity_block()`, `_mood_line()`, `_sample_examples()` and `_voice_reminder()`. It takes the reminder's label and asks for one short line in the buddy's voice. Match its length discipline.
- `tokenpal/brain/orchestrator.py` — spawn generation from `tick()`'s return value:
  - `tick()` is called at `orchestrator.py:783` and its return value is currently discarded; that is your spawn point.
  - `self._nudge_tasks: dict[str, asyncio.Task]` keyed by reminder id, each discarded in an `add_done_callback`. **Keyed, not a set**, because the overlap guard needs it: a reminder whose generation is still in flight when it comes due again must not start a second one — skip and emit the canned label. The nearest precedent, `_conversation_summary_tasks` (declared `:480`, used `:983-988`), is a plain `set` with `add_done_callback(discard)`; copy the discard idiom, not the container.
  - **Hold the reference.** Three of the existing `create_task` sites (`:567`, `:585`, `:602`) keep none, so "match the precedents" would reproduce the GC-mid-flight bug. The dict above is the rule.
  - `asyncio.wait_for(..., timeout=_NUDGE_TIMEOUT_S)`; on `TimeoutError` or any exception, emit the canned label. The in-repo pattern for a bounded await over off-loop work is `_new_conversation_session` (`:992`), which uses `shield` plus a `TimeoutError` fallback — read it before writing this.
  - Deliver through p4's funnel as `self._emit_nudge(label, generated=text)` — see `## Carried in from p4`. **The `generated=True` form this line used to name does not exist:** p4 shipped `generated: str | None`, because a bool leaves no label to fall back to when the filter drops the generation. `generated=None` is the fallback path, so you do not branch on it yourself.
- `tests/test_actions/test_focus/test_proactive.py` — **added at execution.** It constructs `ProactiveScheduler`, so removing `ui_callback` broke it; retargeted onto `tick()`'s return.
- `tests/test_actions/test_reminder.py` — **added at execution.** Same cause: its `_wire()` fixture passed `ui_callback`, so all 22 of its tests raised `TypeError` at construction. The plan predicted two retargeted test files; there were four.
- `tests/test_brain/test_nudge_emission.py` — extend:
  6. a drifted generation (use a `_META_MARKERS` entry from `tokenpal/util/text_guards.py:12`, e.g. `"wikipedia"`) is rejected by `filter_response` and the canned label ships instead.
  7. generation raising, timing out, and returning text that filters to `None` each produce the canned label.
  8. with a generator that never completes, `tick()` still returns and the loop continues — writable as a plain async test because `tick` is sync and the orchestrator owns the spawn (`asyncio_mode = "auto"`, `pyproject.toml:120`).
  9. a second due-fire while the first generation is in flight does not start a second generation and emits the canned label.
  10. the nudge prompt interpolates `_identity_block()` and `_voice_reminder()` the way `build_git_nudge_prompt` does — "in the buddy's voice" is otherwise untested, since "text differs" only proves non-constant.
- `docs/claude/brain.md` — the ProactiveScheduler entry. It has never mentioned this subsystem (`grep -rn "ProactiveScheduler" docs/` returns nothing; `grep -i proactive` hits only "Proactive git nudge" at `:21`, a different subsystem). The file is 23 lines, one `#` heading and a flat bullet list — so this is **a top-level bullet with sub-bullets, matching the shape of the utility-wedges bullets around `:15-21`**, not a `##` section. Cover: the schedule model, wall clock and fire-once-on-wake, the pause gates, the separate emission funnel and why it is exempt from the rate cap and from dedupe, and off-loop generation.
- `CONTEXT.md` — rewrite `:108-112`, whose closing line is "it self-emits through `ui_callback` and never goes through the riff pipeline". That is now false: nudges go through `filter_response` and TTS. `:37-38` ("a multi-tenant scheduler that self-emits on its own clock") is **still true and must not be touched** — the not-a-Wedge ruling survives this plan intact.
- `CLAUDE.md` — one Privacy line: `memory.db` now holds a `reminders` table of user-authored labels and schedules, filtered by the narrow `contains_sensitive_content_term` on the way in and rejected rather than redacted (the broad app list would refuse ordinary self-care wording like "take a health break"). The existing bullet enumerates what reaches `memory.db` and goes false without it.

## Decisions & findings
### Decision: the scheduler is a pure clock; the brain owns delivery  *(operator, 2026-09-05)*
**This resolves a defect in p4's and p5's Work, not a choice left open.** p4 wired `ProactiveScheduler(ui_callback=self._emit_nudge)`, so `tick()` speaks the canned label inline (`proactive.py:230`); p5 then spawns generation from `tick()`'s return and delivers through the same funnel. Both together emit **two bubbles and two utterances per fire**. The Done criteria are all technically satisfiable under the double-emit, which is why no audit caught it.

- **`ui_callback` leaves `ProactiveScheduler.__init__`** and the `try/except` delivery block leaves `tick()`. `tick()` becomes: decide what is due, write through, return it. It emits nothing.
- **`Brain._fire_due_nudges` owns delivery**, exactly once per fire, through `_emit_nudge(label, generated=text_or_None)`.
- **The canned label no longer appears at fire time.** A fire is silent for the generation's duration (bounded at `_NUDGE_TIMEOUT_S`), then speaks once — the voiced line, or the canned label on timeout, exception, or a filtered-out generation. **Operator signed this off knowing the delay**, having seen the timeline.
- **`CONTEXT.md:37-38` stays untouched.** "A multi-tenant scheduler that self-emits on its own clock" remains true in the sense the ruling is about: it fires on its own clock rather than proposing an `EmissionCandidate` for the one-per-tick wedge slot. The not-a-Wedge ruling survives. Do not rewrite that passage; `:108-112` is the one this plan edits.
- **13 pre-existing tests are retargeted, not deleted:** 9 in `tests/test_actions/test_focus/test_proactive.py` (assert on `tick()`'s return list rather than a bubble sink) and 4 in `tests/test_brain/test_nudge_emission.py` (call `_emit_nudge` directly rather than routing through `tick()`). p4's `test_brain_wires_the_scheduler_to_the_nudge_funnel` is replaced by a pin on `_run_loop` calling `_fire_due_nudges`, which is the equivalent single production line for this phase.
- **`proactive.py`'s module docstring rule 1 and its `ui_callback` parameter doc both go stale** and must be rewritten: the scheduler no longer delivers anything.


### Decision: bound the generation even though it is off-loop  *(status: active)*
- **Rationale:** off-loop removes the stall, not the unboundedness; the only inherited ceiling is a per-read HTTP timeout that config cannot reach and a trickling proxy defeats.
- **Evidence:** `http_backend.py:42,425`; `config/schema.py:107-131`; `llm/registry.py:42-59`; probe recorded in the master.

### Decision: one in-flight generation per reminder id  *(status: active)*
- **Rationale:** a 60-second interval and a slow model would otherwise stack tasks without bound, all writing to the same bubble.
- **Alternatives considered:** a global semaphore — rejected, it would serialise unrelated reminders for no benefit.

### Findings from execution  *(2026-09-05)*
- **Moving delivery off the fire broke two guarantees that used to hold for free, and neither was in the Work list.** Both were caught by review, both are now re-made in `Brain._deliver_nudge`:
  1. **The pause gate.** `_proactive_paused` is checked inside `tick()`. Under p4, `_emit_nudge` ran inline in that same call, so the window was zero. Now delivery is up to `_NUDGE_TIMEOUT_S` later: a sensitive app opened in that window would have been spoken over with ambient audio on. Every other cap-bypassing emitter re-checks immediately before emitting; this one now does too, and a suppressed nudge is dropped rather than queued.
  2. **Bubble spacing.** `MIN_NUDGE_GAP_S` spaces *fires*, but the bubble now lands a variable generation later. Two fires 16 s apart whose generations differ by more than a second land inside the bubble's 15 s linger, and the user reads only the second — the exact outcome the operator's 2026-09-05 "nudges must not overwrite each other" decision forbids. The constant is now applied twice, to different things: at fire time it bounds how often a generation is spawned; at delivery it spaces the bubbles. It was made public (`MIN_NUDGE_GAP_S`) because two modules read it.
- **Delivery is serialised behind `_nudge_delivery_lock`** and the overlap branch spawns rather than delivering inline, so a raise cannot escape into the brain loop. `_loose_nudge_tasks` holds those delivery tasks; three of the repo's five existing `create_task` sites hold no reference, which is the bug this avoids.
- **`_NUDGE_TIMEOUT_S` is a soft bound, not a cap.** `asyncio.wait_for` cancels the awaited coroutine and then *waits for it to unwind* — probed at 1.05 s for a 0.05 s timeout against a cancellation-resistant coroutine. So a generation can outlive 15 s if `HttpBackend.generate`'s POST is slow to abort. Not a corruption risk (the only await is the POST, and `_record_sample` runs after it, so the EWMAs are untouched by cancellation) — but the docs say "soft bound" now, and this is the one thing that makes the in-flight overlap guard reachable.
- **Ordering: completion order, not fire order.** Two concurrent generations deliver in whichever finishes first; ties break FIFO by spawn order. Measured. Under the shipped constants they cannot overlap at all (16 s fire gap vs 15 s nominal timeout), so the guard is unreachable from the loop and reachable only by direct call or by the soft-bound case above.
- **A source-text assertion is not a pin.** The first attempt at pinning this phase's one production wiring line used `inspect.getsource`, and passed against `pass  # self._fire_due_nudges() removed`. It now drives one real `_run_loop` iteration with a spy that stops the loop. **This is the third phase in this plan where a single production wiring line was left unpinned** — p4's was reverted with 2446 tests still green. Drive the code; do not grep it.
- **Two prompt templates and one init line had no coverage at all.** `_FINETUNED_REMINDER_NUDGE_TEMPLATE` could be replaced with `MUTANT GUTTED` green (every test ran with `is_finetuned` False); `{voice_reminder}` could be deleted green (a bare `persona_prompt` yields an empty `_voice_reminder()`, so asserting against it was vacuous — catchphrases need a `VoiceProfile`); and `Brain.__init__`'s `_nudge_tasks` line could be deleted green, which in production would `AttributeError` out of `_fire_due_nudges` every 2 s.

## Failure modes to anticipate
- **Ordering is no longer guaranteed.** Two nudges firing in the same tick resolve in whatever order their generations complete. Record the resulting order in your report; do not assume tick order survives.
- Off-loop generation makes a nudge *more* likely to land mid-utterance and stomp an in-flight bubble (`ui/qt/overlay.py:1180,1189-1191`). Not fixed here; note it.
- `HttpBackend` keeps shared mutable EWMA state (`:67-71`); a nudge generation overlapping the session summariser makes each attribute the other's queueing delay to its own TTFT. Parking lot — but do not add a further concurrent caller casually.
- The real serialisation point is the inference server, not the client: an off-loop generation issued while the summariser is mid-call queues there, and that wait counts against `_NUDGE_TIMEOUT_S`.
- Do not let the "server stopped" live check kill the operator's running backend. Hand that check to them.

## Done criteria
- Two fires of the same reminder produce **different** text, and both are spoken with `[audio] speak_ambient_enabled = true`.
- With the inference server stopped, a due nudge still fires the canned label and `_run_loop` keeps ticking — sense polling and chat replies stay responsive throughout. Operator-run, on their backend.
- A drifted generation is replaced by the canned label rather than shipped.
- A second due-fire during an in-flight generation starts no second task.
- `docs/claude/brain.md` has a ProactiveScheduler bullet; `CONTEXT.md:108-112` no longer claims the riff pipeline is bypassed while `:37-38` is unchanged; `CLAUDE.md`'s Privacy bullet names the `reminders` table.
- `pytest` green; `ruff check tokenpal/` and `mypy tokenpal/ --ignore-missing-imports` clean.


## Carried in from p1  *(2026-09-04, do not rediscover)*
- **`docs/claude/brain.md:22` is stale independently of this plan** and this plan makes it worse. It reads "Two migrations so far: session_summaries (v0→v1), active_intent (v1→v2)"; migrations 3 and 4 had already landed before p1, and p1 adds the fifth. It is the doc `CLAUDE.md` routes migration authors to, and `CURRENT_SCHEMA_VERSION` **is** the list index, so a wrong count there misleads directly. Correct it to the real list while writing the ProactiveScheduler entry.
- **The `CLAUDE.md` privacy line must name the retention carve-out, not just the table.** `CLAUDE.md:56` currently says chat content reaches `memory.db` "only through the persisted chat log and conversation summaries". `reminders.label` is user- and model-authored free text that is swept by neither `_prune()` (deliberate — a month-old armed reminder must not vanish) nor `/clear` → `clear_conversation_summaries`. Say both things: the table exists, and it is exempt from retention and from `/clear` by design, so a reminder persists until it is disarmed.


## Carried in from p4  *(2026-09-05, do not rediscover)*
- **The funnel's signature is `Brain._emit_nudge(label: str, *, generated: str | None = None)`**, not the `generated: bool` p4's Work proposed. Your call site is `self._emit_nudge(nudge.label, generated=text)` where `text` is the model's output, or `None` if generation failed or timed out. Passing `generated=None` emits the canned label unfiltered, which is exactly the fallback path — you do not need to branch on it yourself.
- **Only generated text is filtered, and the label is the fallback when the filter chooses silence.** Do not add a second `filter_response` call; the funnel already holds the drift guard, and `filter_response` cannot return `""` (every drop path returns `None`).
- **Do not add a second TTS call.** `_emit_nudge` speaks through `_speak_ambient`; a generated nudge that also called `_speak_async` would double-utter.
- **`tick()` returns the live `ScheduledNudge` objects held in `_nudges`**, and `register()` replaces the dict entry while `cancel()` pops it. A generation task holding a returned reference can outlive the reminder it describes — pass the id and the label into the task, not the object.
- **`tick()` swallows every exception from `ui_callback`** (`proactive.py:229-234`), so an off-loop task that emits through the funnel will not surface its own failures there. Log them where you spawn.
- **`filter_response` stamps `personality.last_filter_reason` on every call.** Read it after the call for a diagnostic rather than re-deriving why text was dropped; its only other production reader is `idle_runner.py`, on the line immediately after its own call, so there is no interleaving window on the brain loop.
