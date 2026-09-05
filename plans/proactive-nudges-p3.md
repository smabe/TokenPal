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
- `tokenpal/brain/orchestrator.py` — exclude the reminder tool from `_build_ambient_specs` (`:1863-1875`) **by action name**. Locked, not a choice: a new `ClassVar` on `AbstractAction` would falsify `docs/claude/actions.md:24` ("`reads_desktop_content` is the only switch; everything below keys on it"), which is the author checklist every future tool author reads and which no shard edits — and CLAUDE.md's "don't abstract on the first use" says a sixth switch for one consumer is not earned. Do **not** widen the predicate to "every action with side effects" either; that is a larger behavioural change than this phase describes.
- `tokenpal/actions/focus/reminders.py` — delete. All four actions and `_ReminderBase` go.
- `ReminderAction` defines **no `teardown`**. `Brain._teardown_components` awaits `action.teardown()` on every action at shutdown (`orchestrator.py:2869-2871`); the `AbstractAction` default is a no-op, and it must stay that way. Only an explicit `cancel` unpersists — quitting must never delete an armed reminder.
- `tokenpal/actions/focus/__init__.py` — the docstring (`:3-6`) names all four; rewrite it for what remains.
- `docs/agents-and-tools.md` — the Focus row (`:67`) is the only file in the repo naming all four tool names; it goes false the moment they are deleted, so it moves in this phase rather than with the rest of the docs.
- `tokenpal/actions/catalog.py` — replace the four `FOCUS_SECTION` rows (`:182-201`) with one `CatalogEntry("reminder", ...)`. Both directions matter: a registered action with no entry fails `test_every_registered_action_has_a_catalog_entry` (`tests/test_desktop/test_privacy_contract.py:314`), and an entry with no action leaves a dead checkbox in `/tools` that nothing asserts against.
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

## Failure modes to anticipate
- `_inject_brain_deps` (`orchestrator.py:852-885`) fills `_scheduler` by attribute-sniffing for an attribute that exists and is `None`. The new action must declare `self._scheduler: ProactiveScheduler | None = None` in `__init__` or injection silently skips it and every call reports "needs a running brain".
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
