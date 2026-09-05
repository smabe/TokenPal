# proactive-nudges-p3 — one `reminder` tool replacing the four

You are phase `p3` of the `proactive-nudges` plan. This phase delivers, as one commit, a single `reminder` action covering arm / disarm / list, deletes the four old reminder actions, and keeps the unprompted ambient LLM out of it. p1 shipped `Schedule` and the `reminders` table; p2 shipped the scheduler that owns them. Use both.

## Locked decisions
See the master `plans/proactive-nudges.md`. Binding here:
- **One tool, not four** (operator decision 1). This deliberately contradicts recorded intent: `plans/shipped/pal-improvement-grand-plan.md:106` justified separate checkboxes "so users can enable pomodoro without also signing up for water reminders". That granularity moves from the `/tools` picker to runtime arm/disarm. Pomodoro is untouched — it is a separate action that uses `asyncio.sleep`, not the scheduler.
- **The ambient observation LLM may not arm or disarm** (operator decision 7). `_build_ambient_specs` (`orchestrator.py:1863-1877`) filters `reads_desktop_content` and `requires_confirm`; the reminder tool is neither, so it needs an explicit exclusion.
- **`requires_confirm = False`.** `resolve_actions` (`registry.py:53-72`) never instantiates an opt-in tool unless its name is in `[tools] enabled_tools`, and `DEFAULT_TOOLS` (`schema.py:216`) is only `timer, system_info, open_app, do_math` — the picker is the arming. A modal would ask twice, and would also prompt on disarm.
- **A user-supplied label is rejected, not redacted, on a sensitive term — using the NARROW list, `contains_sensitive_content_term` (`personality.py:293`).** Operator decision 2026-09-04, taken against measured evidence. The broad `contains_sensitive_term` matches all of `SENSITIVE_APPS` as bare substrings, including eleven ordinary English words (`calm chase fidelity fitbit headspace health keeper keychain messages myfitnesspal signal`), so it refuses `"take a health break"`, `"stay calm"` and `"check messages"` — precisely the vocabulary a self-care nudge is made of. Probed on this Mac: broad refuses all three, narrow allows all three and still refuses `"change 1password master password"`, `"move money in venmo"` and `"reply on whatsapp"`.
  **Accepted consequence, stated rather than hidden:** the narrow list also allows `"pay chase bill"` and `"fidelity rollover"`. A reminder label is text the user deliberately typed into their own 0o600 database — unlike a filename surfaced *to* the model, which is why `find_files` went the other way in #53 and widened its path filter. Different direction of trust, different list.
- **Rule-based parsing in code; the model fills slots.** `plans/brainstorm/senses-tools-ml.md:61` reached this conclusion already. `Schedule` owns validation (p1); the tool turns its `ValueError` into a refusal.

## Work
- Scope trace: DIRECT — the requested outcome's tool half.
- `tokenpal/actions/reminder.py` — new. Shape (proposal):
  ```python
  @register_action
  class ReminderAction(AbstractAction):
      action_name = "reminder"
      description = ("Arm, cancel or list recurring nudges. Schedules are either "
                     "every N minutes or a daily time like 22:30.")
      parameters = {  # JSON Schema
          "action": enum ["arm", "cancel", "list"] (required),
          "label": str,            # what to say; required for arm
          "every_min": int,        # arm, interval form
          "at": str,               # arm, daily form, "HH:MM"
          "id": str,               # cancel; also accepted on arm to replace
      }
      safe = False; requires_confirm = False; cacheable = False
  ```
  `execute` order, each refusal a one-line `ActionResult(success=False)`: unknown/missing `action` → refuse naming the allowed values; `arm` with neither `every_min` nor `at`, or with both → refuse naming the two forms; `label` missing or blank on `arm` → refuse; `contains_sensitive_content_term(label)` → refuse **without echoing the label**; build the `Schedule` and turn its `ValueError` into the refusal text verbatim; assign an id, by two distinct rules — **no `id` supplied:** slug the label, and on collision suffix `-2`, `-3`, so arming "stretch" twice gives two reminders; **`id` supplied:** replace that reminder outright and say so in the reply. Conflating them would make `arm label="stretch"` silently destroy an existing reminder, since `upsert_reminder` is `INSERT OR REPLACE`; register with the scheduler and persist. `cancel` with an unknown id → say so rather than failing silently. `list` with nothing armed → a plain "nothing armed" line; otherwise one line per reminder, formatted **in this module** (not on `Schedule`, which stays a value type), shaped `<id>  <label>  <schedule in words>  next <YYYY-MM-DD HH:MM>` — e.g. `stretch  Stretch break -- stand up.  every 60 min  next 2026-09-04 18:30`.
  **The enum in the schema and the parser must agree** — today's actions advertise `["on", "off"]` but accept `"stop"` and `"cancel"` at runtime (`reminders.py:70,107`). Follow the rule, not a file: derive the schema's `enum` from the same module constant `execute` re-validates against. There is no clean precedent to copy — `memory_query.py:105` writes `list(_ALLOWED_METRICS)` while `execute` checks `_DISPATCH` (`:115`), two constants kept in sync by hand.
- `tokenpal/brain/orchestrator.py` — exclude the reminder tool from `_build_ambient_specs` (`:1866`, measured at `cbc3b25`) **by action name**. Locked, not a choice: a new `ClassVar` on `AbstractAction` would falsify `docs/claude/actions.md:24` ("`reads_desktop_content` is the only switch; everything below keys on it"), which is the author checklist every future tool author reads and which no shard edits — and CLAUDE.md's "don't abstract on the first use" says a sixth switch for one consumer is not earned. Do **not** widen the predicate to "every action with side effects" either; that is a larger behavioural change than this phase describes.
- `tokenpal/actions/focus/reminders.py` — delete. All four actions and `_ReminderBase` go.
- `ReminderAction` defines **no `teardown`**. `Brain._teardown_components` awaits `action.teardown()` on every action at shutdown (`orchestrator.py:2871-2881`, measured at `cbc3b25`); the `AbstractAction` default is a no-op, and it must stay that way. Only an explicit `cancel` unpersists — quitting must never delete an armed reminder.
- `tokenpal/actions/focus/__init__.py` — the docstring (`:3-6`) names all four; rewrite it for what remains.
- `docs/agents-and-tools.md` — the Focus row (`:67`) is the only file in the repo naming all four tool names; it goes false the moment they are deleted, so it moves in this phase rather than with the rest of the docs.
- `tokenpal/brain/agent.py` — **added at execution (review finding, SAFETY).** See Findings below: the desktop-content gate covers network tools only, so the new tool was a durable local sink reachable straight after a `read_selection`.
- `README.md` — **added at execution (review finding).** `:214` still named water/stretch reminders as shipped tools.
- `docs/claude/actions.md` — **added at execution (review finding).** `:7`'s tool-author checklist stated `requires_confirm` as the only ambient exclusion, which this phase falsifies, and it now documents `_PERSISTENT_SINKS`.
- `tokenpal/actions/catalog.py` — replace the four `FOCUS_SECTION` rows (`FOCUS_SECTION` at `:173`, measured at `cbc3b25`) with one `CatalogEntry("reminder", ...)`. Both directions matter: a registered action with no entry fails `test_every_registered_action_has_a_catalog_entry` (`tests/test_desktop/test_privacy_contract.py:314`), and an entry with no action leaves a dead checkbox in `/tools` that nothing asserts against.
- `tests/test_actions/test_catalog.py` — **add** a `FOCUS_SECTION` name pin. There is nothing to update: the file pins `DEFAULT_SECTION` (`:8-14`), `LOCAL_SECTION` (`:17-29`) and section ordering (`:37-39`) only.
- `tests/test_actions/test_focus/test_reminders.py` — delete with the actions it tests.
- `tests/test_actions/test_focus/test_brain_injection.py` — retarget off `StretchReminderAction` (`:10,27,47`) onto `ReminderAction`; it pins that `_inject_brain_deps` fills `_scheduler`, which still matters.
- `tests/test_actions/test_reminder.py` — new:
  1. `arm` with `every_min` and with `at` → registered, persisted, and the reply names the next fire.
  2. `arm` with neither, with both, with a bad `every_min`, with a malformed `at` → refused, each naming the offending argument, nothing registered.
  3. `arm` with a sensitive label → refused, and the label does **not** appear in the output. Use a narrow-list term (`1password`, `venmo`, `whatsapp`), **not** a bank name — `chase` and `fidelity` are deliberately allowed now.
  3b. The false-positive regression this decision exists to prevent: `"take a health break"`, `"stay calm"` and `"check messages"` each arm successfully. Without this case a future switch back to the broad list would pass every other test.
  4. `cancel` by id removes and unpersists; `cancel` on an unknown id says so.
  5. `list` empty, and `list` with two armed showing both ids and schedules.
  6. the schema's `action` enum equals the constant `execute` validates against.
  7. **`reminder` is absent from `_build_ambient_specs` and present in `_build_conversation_specs`** — the operator's decision 7, pinned.
  8. `reminder` is absent from `DEFAULT_TOOLS` and present in `FOCUS_SECTION` (`catalog.py:173`) — **not** `LOCAL_SECTION` (`:52`), which is the read_file/grep/git set.

## Decisions & findings
### Decision: the reminder id is separate from the tool name  *(status: active)*
- **Rationale:** today `register(name=self.action_name, ...)` (`reminders.py:87`) makes the nudge key *be* the tool name, which is exactly why four schedules needed four tools and why you cannot arm two stretch reminders. A user-facing id gives `list` something to print and persistence something to key on.
- **Evidence:** cited inline.

### Decision: no config rewriter for stale `enabled_tools` names  *(status: active)*
- **Rationale:** a stale name is silently ignored (`registry.py:53-72` iterates the registry) and the next `/tools` save drops it, because `set_enabled_tools` rewrites the whole list. A user who had `water_reminder` on loses it with no message — worth a release note, not a migration.
- **Evidence:** `tokenpal/config/tools_writer.py:17-24`; `app.py:1130-1186`. This machine is unaffected: `config.toml:41-63` lists none of the four and `~/.tokenpal/config.toml` does not exist.

### Findings from execution  *(2026-09-05)*
- **The new tool opened a desktop-content leak, confirmed by probe.** `/agent` could call `read_selection` and then `reminder(action="arm", label=<what it read>)`. `AgentRunner`'s post-content gate drops only **consent-gated (network)** tools, and `reminder` has `consent_category=""`, so it survived — while being a *durable local sink*. `reminders` rows are exempt from `_prune` **and** from `/clear`, so the residue is permanent and no user-facing wipe reaches it. `assert_no_leak` tripped on the first probe. `brain/agent.py` gained `_PERSISTENT_SINKS`, gated **on both sides**: dropped from the advertised specs AND refused at execution, because the model can call a sink in the same batch as the read, or re-emit a name it saw before the drop. The advertise-side-only first attempt was proven insufficient by the closing round.
- **Operator decision 2026-09-05: `habit_streak` and `mood_check` join that set by name.** Both write model-authored free text to `habit_log` / `mood_log`, both have `consent_category=""`, and both are swept by neither `_prune` nor `/clear` — the identical hole, pre-existing. The operator chose the name set over a `writes_durable_state` ClassVar, so `docs/claude/actions.md` now says explicitly that nothing derives the set for you.
- **The `try/except ValueError` around `register()` is NOT dead — do not delete it again.** One reviewer proved it unreachable across the parser's output space and it was removed; the closing round then constructed the reachable case. A model emitting JSON `"\ud800"` yields a lone surrogate that passes `contains_sensitive_content_term`, `_text_arg` and `_slug`, and raises `UnicodeEncodeError` — a `ValueError` subclass — at sqlite bind. `register()` writes `_nudges` **before** persisting, so the handler must roll the in-memory nudge back or the reminder stays armed with no row behind it. Pinned by `test_an_unstorable_label_refuses_and_leaves_nothing_armed`.
- **Collapsing four fixed tools into one removed a structural bound.** Each old action registered under `id=self.action_name`, so re-arming replaced and the armed set was capped at four. One parameterised tool mints a fresh id per call; 200 successive arms all succeeded, all persisted, all hydrating at launch. `MAX_ARMED = 20`, and replacement at the cap still works.
- **Everything the model authors that reaches a sink needs a bound, not just the label.** A 200,000-character `label` was accepted and echoed into the tool result, the bubble and every `list` line; the `id` had the same reach and was missed by the first fix. `MAX_LABEL_LEN = 200`, `MAX_ID_LEN = 60`.
- **`list` renders one reminder per line and is the model's only view of armed state**, so an interior newline in a label fabricated a row. `_text_arg` collapses whitespace runs, which also stops a label forging the two-space field separator. Every `str.splitlines()` separator — including NEL, LS and PS — is matched by `\s`.
- **The shutdown-does-not-unpersist guard was deleted with `test_focus/test_reminders.py`** and had to be re-established here. It is the only test in the suite that catches a reintroduced action-level `teardown()` calling `cancel()`, which would wipe every armed row on every quit.
- **`_slug` collapsed every non-Latin label to the literal id `reminder`.** Now `[^\w]` with `re.UNICODE`, so a Japanese or Arabic label keeps a meaningful id.
- **The refusal keeps a sensitive label out of the reply and the DB, but not out of the log file.** `_execute_tool_call` logs raw tool arguments at DEBUG and the file handler is unconditionally DEBUG (`util/logging.py:41`), so a refused label still reaches `~/.tokenpal/logs/tokenpal.log`. That line is generic to every tool and predates this work; the source comment was narrowed rather than special-casing one tool in a shared path. Recorded in the master Parking lot.

## Failure modes to anticipate
- `_inject_brain_deps` (`orchestrator.py:863`, measured at `cbc3b25`) fills `_scheduler` by attribute-sniffing for an attribute that exists and is `None`. The new action must declare `self._scheduler: ProactiveScheduler | None = None` in `__init__` or injection silently skips it and every call reports "needs a running brain".
- The `config.get("scheduler")` route in `_coerce_scheduler` (`reminders.py:33-38`) is **dead** — `resolve_actions` accepts `action_configs` but no production call site passes it (`app.py:227-231`, `cli.py:155-159`). Do not carry it into the new action; use the injection route, which already handles `_llm` too (`orchestrator.py:864-865`) and is what p4 needs.
- The two id rules above are the whole of the collision policy; pin both in tests. Two reminders silently sharing an id would make `cancel` ambiguous and `INSERT OR REPLACE` destructive.
- **The action count will not drop.** `tokenpal --check` reports 22 actions today and none of the four appear, because `_check_actions` (`cli.py:142-167`) passes `config.tools.enabled_tools` as the allowlist and `config.toml:40-63` lists none of them. Add `reminder` to `enabled_tools`, then the count goes 22 → 23. `discover_actions` swallows `ImportError` at DEBUG (`registry.py:31-34`), so a broken import in the new module reads as a missing tool rather than a crash — check the name is present, not just that nothing errored.

## Done criteria
- On this Mac, with `reminder` enabled in `/tools`: arm one from chat, `list` shows it with its next fire time, `cancel` removes it, and `list` then reports nothing armed.
- `grep -rn "stretch_reminder\|water_reminder\|eye_break\|bedtime_wind_down" tokenpal/` returns nothing.
- With `reminder` added to `[tools] enabled_tools`, `tokenpal --check` lists it and the count is 23; none of the four old names appear (they never did on this machine — they were not in the allowlist).
- A sensitive label is refused and does not appear in the refusal text.
- `reminder` is in the conversation spec list and not in the ambient one, asserted by test.
- `pytest` green; `ruff check tokenpal/` and `mypy tokenpal/ --ignore-missing-imports` clean.


## Carried in from p1  *(2026-09-04, do not rediscover)*
- **`Schedule` owns parsing and its messages are the refusal strings.** `interval_from_minutes` and `daily_from_hhmm` raise `ValueError` naming `every_min` / `at`. Surface the message verbatim. Both grammars are strict on purpose: `"1e3"` and `"1_0"` are refused (they used to parse as 1000 and 10 minutes), and `"9:3"` is refused rather than silently armed as 09:03.
- **`upsert_reminder` raises `ValueError` on a schedule mapping it cannot read back.** The tool must catch it and refuse, not let it escape the executor.
- **`delete_reminder` returns `False` for BOTH "no such reminder" and "memory disabled or closed"** (`[memory] enabled = false`, or a call after teardown). Do not phrase the refusal as "you have no reminder called X" without accounting for that, or the buddy tells a user their reminder was never set when the store was simply off.
- **Reminder labels are now swept by `assert_no_leak`** (`tests/_helpers.py`), because `reminders.label` is persistent free text the model can write. Keep it that way when the tool lands.
- **The four old reminder actions' constants and parsing are duplicated in `schedule.py` by design, and this phase's deletion is what resolves it.** `_MIN_INTERVAL_MIN`/`_MAX_INTERVAL_MIN` (`actions/focus/reminders.py:27-28`), the `%H:%M` parse (`:198`) and the roll-to-tomorrow maths (`:239-249`) all exist twice while both live. Treat the deletion as load-bearing, not optional: if it slips, changing the interval bounds leaves two layers disagreeing with no test catching it.


## Carried in from p2  *(2026-09-05, operator-routed)*
- **An armed reminder currently outlives the only tool that can disarm it — close this here.** `ProactiveScheduler.hydrate()` loads every row unconditionally, but the disarm path lives inside the action's `execute`, and `resolve_actions` (`registry.py:63-65`) never instantiates an action absent from `[tools] enabled_tools`. So arming `stretch_reminder`, then un-ticking it in `/tools`, leaves a reminder that fires every launch forever with no in-app way to stop it — `memory.db` must be hand-edited. Two reviewers found this independently at the p2 gate. **Operator decision 2026-09-05: fix it in p3, not p2.** It also inverts the consent story in `CLAUDE.md`, where enabling a tool in the picker IS the consent.
  **Settled 2026-09-05, do not re-decide: gate the hydrate, never delete the rows.** `Brain.start()` calls `self._proactive.hydrate()` at `orchestrator.py:570`; guard it with `if "reminder" in self._actions:` — `self._actions` is a dict keyed by `action_name`, built at `:363` from what `resolve_actions` returned, so it is exactly "the tool the user has enabled". Disarming the rows instead would make un-ticking a checkbox silently destroy the user's reminders, and re-ticking it would not bring them back; gating the hydrate is reversible and matches the picker-is-the-consent story. The rows stay on disk, dormant.
  **Done criterion:** with `reminder` absent from the resolved action set, an armed row hydrates nothing and fires nothing, and the row is **still in `memory.db`** afterwards; re-enabling the tool restores it on the next launch. Assert all three.
- **`tick()` returns `list[ScheduledNudge]` holding at most one element.** Do not write `for n in sched.tick():` expecting several; the list is a seam for p5's per-nudge generation task, and one-per-tick plus `_MIN_NUDGE_GAP_S` is deliberate.
- **`register()` preserves `last_fired_at` only when the id is already in the in-memory dict.** After `hydrate()` that always holds, but a `list` tool should read fire history from `memory.list_reminders()` rather than trusting `armed()` alone, or it can report "never fired" for a reminder the row says fired.
- **`delete_reminder` returns `False` for both "no such reminder" and "memory disabled or closed"**, so do not phrase a refusal as "you have no reminder called X" without accounting for the store being off.
- **`MIN_INTERVAL_MIN` / `MAX_INTERVAL_MIN` are public on `tokenpal/brain/schedule.py`.** Use them; do not restate 1/1440 in the tool.
