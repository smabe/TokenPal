# proactive-nudges-p2 — scheduler owns the schedule, on a wall clock

You are phase `p2` of the `proactive-nudges` plan. This phase delivers, as one commit, a `ProactiveScheduler` that owns a `Schedule` instead of a closure, runs on the wall clock, hydrates from `memory.db` at start, and fires a missed occurrence once on wake. The four existing reminder actions are **adapted, not removed** — p3 replaces them. p1 shipped `tokenpal/brain/schedule.py` and the `reminders` table; use them.

## Locked decisions
See the master `plans/proactive-nudges.md`. Binding here:
- **The scheduler owns the schedule and asks for text only once it has decided to fire.** Today `message_fn: Callable[[], str]` (`proactive.py:26,34`) is where the schedule hides, which is why nothing can be persisted.
- **Wall clock throughout the stored state.** In-process pacing may still use `time.monotonic()`, but every due decision and every stored timestamp is `time.time()`.
- **Fire once on wake, never replay** (operator decision 8).
- **`one_shot` and `registered_names` are deleted**, not carried. Verified dead: `one_shot`'s only `True` caller in the tree is its own test (`tests/test_actions/test_focus/test_proactive.py:86`); `registered_names` has zero callers anywhere.
- Emission still goes through the raw `ui_callback` in this phase. The funnel is p4; do not build it early.

## Work
- Scope trace: DIRECT — the requested outcome's scheduler half; PREREQUISITE for p3, which needs `list` to have something real to read.
- `tokenpal/brain/proactive.py` — rewrite. `ScheduledNudge` carries `id`, `label`, `schedule: Schedule`, `next_due_at: float`, `last_fired_at: float | None` — no callable. Shape (proposal):
  ```python
  def __init__(self, ui_callback, is_paused, memory: MemoryStore | None = None)
  # The scheduler owns its own persistence. register/cancel/tick write through
  # `memory` when it is not None (upsert_reminder / delete_reminder /
  # mark_reminder_fired); with None it is purely in-memory, which is what every
  # existing sync test constructs.

  def register(self, *, id: str, label: str, schedule: Schedule,
               next_due_at: float | None = None) -> None
  # next_due_at defaults to schedule.next_due_at(time.time()).

  def cancel(self, id: str) -> bool
  def armed(self) -> list[ScheduledNudge]      # replaces registered_names()

  def tick(self, now: float | None = None) -> list[ScheduledNudge]
  # RETURNS what fired, keeping today's list return (proactive.py:104) rather
  # than dropping it -- p5 needs exactly this to spawn one generation task per
  # fired nudge without the scheduler ever importing asyncio.
  # now defaults to time.time(). CORRECTED AT EXECUTION (operator, 2026-09-05):
  # at most ONE nudge fires per tick, the soonest due, and never within
  # _MIN_NUDGE_GAP_S of the last fire -- the bubble replaces rather than
  # queues, so a relaunch with three overdue reminders showed only the last
  # while marking all three fired. The rest stay due and follow on later
  # ticks. The list return holds at most one element. Fire once, set
  # last_fired_at = now, and set next_due_at = schedule.next_due_at(now) --
  # computed from `now`, NOT from the old next_due_at, so a five-hour gap
  # yields one fire and one future deadline rather than a backlog.
  ```
  **`tick` stays synchronous and asyncio-free.** Every existing test in `test_proactive.py` is a plain `def`; making the scheduler spawn tasks itself would raise `RuntimeError: no running event loop` in all of them and hand p5 no seam.
  Delete `one_shot` (`:36-40,74,83,136`), `registered_names` (`:97-98`) **and `is_registered` (`:94-95`)** — all three are dead. `is_registered`'s only callers are `test_proactive.py:56,58,90` (rewritten in this phase) and `test_reminders.py:30,40,122` (deleted in p3), so update those tests to use `armed()`. Keep the pause gate exactly as it is and **fix the class docstring at `:51-53`**, which claims `is_paused` is "conversation.is_active OR sensitive_app_in_foreground" while the code (`orchestrator.py:887-897`) also covers `_paused` and `_any_long_task`. Keep the existing behaviour that a paused tick does not advance the deadline (pinned by `test_scheduler_pauses_during_conversation`).
  Wrap the delivery call in a `try`: today `self._ui_callback(text)` (`:133`) is outside any `try`, and `tick()` is called bare at `orchestrator.py:762` inside the loop's own `try`, so one raising nudge aborts the rest of that tick — rollover, intent sync, wedges, the observation and the idle roll. A raising nudge must not abort the brain-loop iteration, and must still be reported in `tick`'s return list; with one-per-tick the next nudge follows on a later tick rather than the same one.
- `tokenpal/brain/orchestrator.py` — pass the `MemoryStore` into `ProactiveScheduler` at construction (`:419-422`), and hydrate armed reminders from `memory.db` in `start()`'s startup block, beside `_load_previous_session_note()` (`:566`) and `_maybe_fire_pending_eod()` (`:571`). On hydrate, a reminder whose stored `next_due_at` is already past gets **one** fire opportunity on the next tick and its deadline recomputed from now. Persist `last_fired_at`/`next_due_at` through `mark_reminder_fired` when a nudge fires. Do not add a per-tick database read: hydrate once into the in-memory dict and write through on change — `MemoryStore` is blocking, shares its lock with the Qt thread (`app.py:1789-1795`), and this is the 2-second hot path.
- `tokenpal/actions/focus/reminders.py` — **delete `_ReminderBase.teardown` (`:95-97`) first.** It calls `self._scheduler.cancel(self.action_name)`, and `Brain._teardown_components` awaits `action.teardown()` for every action on shutdown (`orchestrator.py:2869-2871`). Once `cancel` also unpersists, **quitting the app would delete every armed reminder from `memory.db`** — the exact outcome this plan exists to deliver. Also delete the now-dead `_message_override` (`:54`) and `_message_fn` (`:56-60`), which return a closure the scheduler no longer takes. Then adapt the four actions to build a `Schedule` and call the new `register`. `bedtime_wind_down` becomes a real `kind="daily"` schedule: delete `_make_bedtime_message_fn` (`:225-255`) and the `""`-means-skip trick it depends on. Its `interval_min` argument disappears with it — that argument was accepted at `:205` but never advertised in its schema (`:163-172`), so no caller can be relying on it. Each action's canned string becomes the nudge's `label` and each keeps its `action_name` as the reminder `id`. p3 deletes these actions outright, so nothing here is carried forward except the rows: a reminder armed under p2 leaves a row keyed `stretch_reminder` that hydrates, lists and cancels normally under p3's tool. No migration.
  **`bedtime_wind_down` loses its re-nudge window, deliberately.** Today it is not a daily one-shot: `default_interval_min = 15` and `_make_bedtime_message_fn` returns text only inside `0 < (target - now) <= 60 min`, so it fires up to four times across the wind-down hour. A `kind="daily"` schedule fires **once**. Windowing is a master Non-goal and `Schedule` cannot express it. **Operator confirmed 2026-09-04: "repeated nagging isn't the point of the wind down"** — one fire at the target time is the wanted behaviour, not a regression to be mourned. Do **not** treat it as the stop-and-report case below, and do not add windowing to `Schedule` to preserve it.
- `tests/test_actions/test_focus/test_proactive.py` — rewrite onto the schedule model. Keep the injected-`now` discipline. Note it is not currently universal: `test_scheduler_cancel` calls `sched.tick()` bare (`:59`), and every test seeds from `last_fired_at`, whose default is `time.monotonic()` (`proactive.py:35`) — that default becomes `time.time()`. Cases: interval fires and re-arms from `now`; **a five-hour gap fires exactly once, not five times** (the wake-once rule, and the case the whole phase exists for); a paused tick does not advance the deadline and fires the instant the gate reopens; a daily schedule fires at its instant and re-arms for tomorrow; `cancel` removes; `armed()` reports what is registered; a raising delivery does not prevent the next nudge in the same tick from firing.
- `tests/test_actions/test_focus/test_reminders.py` — **added at execution.** Not predicted, but it imports `is_registered` and `_make_bedtime_message_fn` and passes bedtime's `interval_min`, all deleted here, so it could not import. It also falls inside this phase's own done-criteria grep scope. Planning miss: any file naming a symbol the phase deletes is in the phase.
- `tokenpal/actions/catalog.py` — **added at execution (review finding).** The `bedtime_wind_down` picker blurb read "Recurring wrap-up nudges starting 60 minutes before bedtime", false the moment the window went. p3 rewrites `FOCUS_SECTION` wholesale, but the blurb is user-visible in `/tools` today.
- `CONTEXT.md` — **added at execution (review finding).** `:111` described the scheduler as firing "on their own intervals"; it now has a daily kind too. p5 owns `:112`'s riff-pipeline clause; `:111` was unowned by any phase.
- `tokenpal/brain/memory.py` — **added at execution (review finding).** `mark_reminder_fired` returned `False` for both "no such row" and "store closed or disabled"; the scheduler reads `False` as "drop this nudge". See Decisions below.
- `tests/test_actions/test_focus/test_brain_injection.py` — still imports `StretchReminderAction` (`:11`, constructed `:27`, asserted `:29,42`) and pins `stretch._scheduler is brain.proactive`. Keep it working against the adapted action; p3 retargets it.

## Decisions & findings
### Decision: recompute the next deadline from `now`, not from the missed deadline  *(status: active)*
- **Rationale:** it is the whole of decision 8 in one line. Advancing from the old deadline produces the backlog the operator rejected; advancing from `now` gives fire-once and a clean future deadline.
- **Alternatives considered:** storing a "missed count" and draining it — rejected, it is the catch-up behaviour under another name.

### Decision: hydrate once, write through on change  *(status: active)*
- **Rationale:** `_build_idle_context` already needed TTL caches (`memory.py:207-209`) because it runs every 2-3 s; a per-tick reminders query would sit on the same path with a lock shared with the UI thread.
- **Evidence:** `memory.py:200,268-272`; `app.py:1789-1795`.

### Findings from execution  *(2026-09-05)*
- **`mark_reminder_fired`'s `False` had to become unambiguous.** It answered `False` for a deleted row AND for `not self._enabled or not self._conn`, and `tick()` reads `False` as "the row is gone, drop the nudge". `MemoryStore.teardown()` sets `_conn = None` while `enabled` stays `True`, so a tick after memory teardown would silently disarm live reminders — a storage outage disarming the user's promises. It now returns `True` when it has no evidence the row is gone. That also removed the `self._memory = memory if ... memory.enabled else None` collapse in the constructor, which existed only to work around the ambiguity.
- **One nudge per tick was not enough on its own.** `show_speech` replaces the bubble rather than queueing it, so several overdue nudges delivered together leave the user reading only the last — reachable on any relaunch after a long absence, when every armed reminder is overdue. But `poll_interval_s` is 2.0 (`config/schema.py:182`) and `_BUBBLE_HIDE_DELAY_MS` is 15000 (`ui/qt/overlay.py:75`), so one-per-tick alone still overwrites at 2-second spacing. `_MIN_NUDGE_GAP_S = 16.0` gates consecutive fires; the rest stay due and follow on later ticks. **Operator decision 2026-09-05: nudges must not overwrite each other.**
- **The hydrate clamp has to be written back to disk.** A forward clock step that is later corrected leaves a `next_due_at` no schedule could produce; the reminder then never comes due again and survives every restart. Clamping in memory alone re-clamps on every launch, so a user whose session is shorter than the recomputed gap never reaches the fire. `hydrate()` now upserts the repaired deadline.
- **26 h is the right clamp bound, brute-forced not assumed.** Every minute-of-day target across the 2026 spring and fall transitions in `America/New_York`, `Europe/London`, `Australia/Lord_Howe`, `Pacific/Chatham` and `America/Santiago`: maximum daily gap exactly 25.0 h, maximum interval gap 24 h. Zero cases returned `<= after`, so there is no fire-every-tick loop.
- **The four actions had to route through `Schedule`'s parsers.** `bedtime_wind_down` re-parsed with `datetime.strptime("%H:%M")`, whose `%M` matches one digit — reintroducing exactly the `"9:3"` → 09:03 defect p1 tightened `_AT_RE` to prevent. Routing through `Schedule.interval_from_minutes` / `daily_from_hhmm` also fixed two uncaught exceptions: `int(None)` and `int("abc")` previously escaped `execute` as `TypeError`/`ValueError`. `MIN_INTERVAL_MIN`/`MAX_INTERVAL_MIN` were made public on `Schedule` so the action's own message cannot drift from the bounds actually enforced.
- **Three tests the phase needed were absent, each proven by mutation.** Re-adding `_ReminderBase.teardown` — the defect this whole plan exists to prevent — passed the entire suite green. So did hard-coding every interval to 24 h, and so did moving a `list_reminders()` read onto the per-tick hot path. All three now have tests; the teardown one was mutation-verified twice.
- **`tick()` does take `MemoryStore`'s lock**, via `mark_reminder_fired`'s `UPDATE` + `commit` on a fire. The rule is "never re-list per tick", not "never touch the store per tick"; a test named for the stronger claim was renamed to the one it proves.

## Failure modes to anticipate
- **The daily deadline must be recomputed from the local calendar, not by adding 86400.** Across a DST transition the wall-clock day is 23 or 25 hours; `+86400` drifts a daily reminder by an hour twice a year and never self-corrects. p1's `Schedule.next_due_at` already does this — call it, do not reimplement the arithmetic in the scheduler.
- **A stored `next_due_at` from a previous version of the schedule is stale.** When a reminder is re-armed with a different schedule, recompute and persist the deadline in the same write.
- `time.time()` moves under NTP steps. A large backwards correction can push a deadline far into the future; a large forwards one fires everything at once. Fire-once bounds the damage to one bubble per reminder, which is the reason that rule is worth keeping even outside the sleep case.
- Do not "fix" the extra `self._context.snapshot()` rebuild in `_proactive_paused` (`orchestrator.py:894`) here — it is real waste and it is in the Parking lot, but touching the gate in the phase that rewrites the scheduler makes both harder to review.
- The four actions keep working through this phase. If adapting one of them needs a behaviour change beyond swapping the closure for a `Schedule`, that is a signal p3's shape is wrong — stop and report rather than bending the action.

## Done criteria
- A reminder armed with `next_due_at` five hours in the past fires **exactly once** on the next tick and its new deadline is in the future — asserted, with the count, not just "it fired".
- `bedtime_wind_down` runs off `kind="daily"`, and `ProactiveScheduler.tick` contains no "empty message means skip" branch. `grep -rn '_make_bedtime_message_fn|one_shot|registered_names|is_registered' tokenpal/ tests/test_actions/` returns nothing. (Scope the grep to `tests/test_actions/`: `tests/test_brain/test_orchestrator_idle_path.py:193` is an unrelated `test_deliver_riffs_one_shot_...` that must not be renamed.)
- Quit and relaunch on this Mac with a reminder armed: it is still armed afterwards (`list_reminders` shows it) — the first time any state in this subsystem has survived a restart. **Add `stretch_reminder` to `[tools] enabled_tools` in `config.toml` first**; none of the four is in the allowlist today (`config.toml:40-63`), so `resolve_actions` never instantiates them and the check is otherwise unreachable.
- `_teardown_components` runs on that quit and the row is **still** in `memory.db` afterwards — only an explicit `cancel` unpersists, never process shutdown.
- A nudge whose delivery raises does not stop the following nudge in the same tick, and does not abort the rest of the brain-loop iteration.
- `pytest` green; `ruff check tokenpal/` and `mypy tokenpal/ --ignore-missing-imports` clean.


## Carried in from p1  *(2026-09-04, do not rediscover)*
- **`Schedule.next_due_at(after)` rolls forward ONE occurrence.** When re-arming after a fire, pass the **current time**, never the deadline that just fired. Chaining from a stale deadline returns a result still in the past, and the tick would then fire once every 2-3 s until it walked forward to now — one nudge per missed day, which is exactly what this phase's done signal forbids. `tests/test_brain/test_schedule.py::test_next_due_at_from_now_collapses_a_missed_gap` pins the rule; the DST no-drift test deliberately chains from the previous due time and is the one place that pattern is correct, because there the previous value is not stale.
- **`mark_reminder_fired` returns `bool`** — `False` means the row is gone (disarmed from chat between the tick's due-check and its write-through). Act on it: a nudge held in memory whose row has been deleted must be dropped, not left firing from memory with nothing backing it.
- **Hydrate must skip and log an unreadable row, never propagate.** `Schedule.from_row` raises `ValueError` whose message names a *tool* argument (`every_min`, `at`) — a statement about input the user never gave. `upsert_reminder` now validates before writing so this should be unreachable, but a hand-edited or future-version `memory.db` can still produce one, and hydrate runs in `Brain.start()`: one bad row must not stop the brain from starting.
- **Do not add a per-tick `list_reminders()` call.** Hydrate once, write through on change. p1's failure-modes note about a "cheap query" was corrected — it justifies a single read at startup, not one on the 2-second hot path, where `MemoryStore` blocks on a lock the Qt thread also takes.

## Done criteria  *(added from p1's findings)*
- Re-arming a reminder that has already fired preserves its `last_fired_at`; the scheduler's in-memory copy and the row agree after a restart.
- A reminder disarmed from chat during a tick does not keep firing from memory: `mark_reminder_fired` returning `False` drops it.


## Superseded by p5  *(operator, 2026-09-05)*
- **`ui_callback` is no longer a `ProactiveScheduler` constructor parameter and `tick()` emits nothing.** p2 shipped the scheduler as its own emitter; p4 then made the brain's nudge funnel that callback; p5 found the two together produced a bubble per fire *plus* a bubble per generation. The scheduler is now a pure clock — it decides what is due, writes through, and returns it. `Brain._fire_due_nudges` delivers. The nine `test_proactive.py` tests that asserted on a bubble sink assert on `tick()`'s return list instead.
