# harness-tool-policy-p1 — Tools declare whether they may run unprompted

You are phase `p1` of the `harness-tool-policy` plan. This phase delivers, as one commit, a declared `allow_unprompted` flag on `AbstractAction`, an ambient spec filter that reads it, and the deletion of the hardcoded `reminder` name check that filter carries today.

## Locked decisions
See the master `plans/harness-tool-policy.md`. The decisions binding this phase:
- **`allow_unprompted` defaults to `False` — fail closed (operator, 2026-09-05).** A tool is ineligible for unattended ticks until a human opts it in, so tool number 40 is inert by default rather than live. This matches the convention every safety-relevant flag in `base.py` already follows (`safe = False`, `requires_confirm = True`), and unlike `reads_desktop_content = False` there is no possible inverse selector to catch an omission: nothing in a tool's source reveals that it is expensive, slow, or side-effecting, so a permissive default would be fail-open with no safety net.
- **The 26 opt-ins are load-bearing and land in the same commit.** With the default inverted, omitting one silently drops a tool from ambient. The expected-set test below is what makes an omission fail loudly instead.
- **It is additive to the existing filter, not a replacement.** `reads_desktop_content` and `requires_confirm` stay. Those exclusions are mechanical — prompt-only by contract, and a modal would stall the loop (`orchestrator.py:2035-2039`) — and are independent of whether a tool is *suitable* unprompted.
- **The flag is author-declared, not user-configurable.** A `config.toml` ambient list is parked in the master.
- **The `_REMINDER_TOOL` constant stays.** It has a second consumer at `orchestrator.py:660`. Only the `and a.action_name != _REMINDER_TOOL` clause at `:2049` is deleted; `reminder.py` carries the exclusion as a declaration instead.
- **Exclusion list signed off by the operator 2026-09-05:** `research`, `research_followup`, `fetch_url`, `read_file`, `grep_codebase`, `find_files`, `list_processes`, `timer`, `pomodoro`, `reminder`. Ambient-eligible drops 35 → 26.
- **`_PERSISTENT_SINKS` becomes its OWN declaration, not part of `allow_unprompted` (operator approved the conversion 2026-09-05; the non-conflation is a correction made at approval).** `agent.py:396` holds `frozenset({"reminder", "habit_streak", "mood_check"})`, added by `e1693ec` in the same commit as the ambient name check this phase deletes. It is the same hand-written-list *shape*, but a different *policy*: it fires only when `session.desktop_content` is true (`agent.py:299`) and means "writes model-authored text to a durable local sink" — `reminders`, `habit_log`, `mood_log` rows that survive `_prune` and `/clear` (`agent.py:390-395`). It overlaps `allow_unprompted` on `reminder` alone. Merging them would drop `habit_streak` and `mood_check` from ambient — a change nobody asked for — and would lose the desktop-content quarantine that CLAUDE.md's privacy contract requires. So this phase adds a second ClassVar, `writes_durable_sink`, and `habit_streak`/`mood_check` stay ambient-eligible.

## Work
- Scope trace: DIRECT — the master's Goal names "stop the buddy from being offered 35 of its 39 tools on unattended ambient ticks", and this phase is the whole of that outcome.
- `tokenpal/actions/base.py` — add the ClassVar beside the existing policy declarations. Proposed (name and default are proposals; the shape is contract):
  ```python
  # True only when a tool is suitable for an unattended ambient tick. Default
  # False: a new tool is inert until a human opts it in. Independent of
  # requires_confirm, which is about raising a modal.
  allow_unprompted: ClassVar[bool] = False
  # True when the tool writes model-authored text to a durable local sink whose
  # rows survive _prune and /clear. Replaces agent.py's _PERSISTENT_SINKS.
  writes_durable_sink: ClassVar[bool] = False
  ```
- **The 26 tools that declare `allow_unprompted = True`.** This set is the complement of the operator's ten exclusions against today's 35 ambient-eligible actions, computed from the live registry this session:
  ```
  air_quality  book_suggestion  convert
  crypto_price  currency  do_math
  git_diff  git_log  git_status
  habit_streak  hydration_log  joke_of_the_day
  memory_query  mood_check  moon_phase
  on_this_day  pollen_count  random_fact
  random_recipe  sports_score  sunrise_sunset
  system_info  timezone  trivia_question
  weather_forecast_week  word_of_the_day
  ```
  Find each one's module with `grep -rn 'action_name = "<name>"' tokenpal/actions/` — several share a module (`habit_streak` and `mood_check` are both in `focus/logs.py`), so this is 26 declarations across fewer files.
- `tokenpal/brain/orchestrator.py` — `_build_ambient_specs` (`:2033-2050`) gains `and a.allow_unprompted` and loses `and a.action_name != _REMINDER_TOOL`. Rewrite the docstring: the `reminder` paragraph becomes a statement that suitability is declared per tool. Leave `_REMINDER_TOOL` (`:211`) defined for its consumer at `:660`.
- `tokenpal/actions/reminder.py` — `allow_unprompted = False` (already the default, declared explicitly for the comment) and `writes_durable_sink = True`. This is the declaration that replaces the deleted name check; the existing rationale at `orchestrator.py:2041-2042` ("an unprompted tick must not arm or disarm a standing commitment") moves here as the comment. Also trim the `_REMINDER_TOOL` comment at `orchestrator.py:208-210`, which documents two rules keying on the constant — after this phase only the second (armed rows hydrate while the cancelling tool is enabled) is still true.
- `tokenpal/actions/research/research_action.py` — no declaration needed on either `ResearchAction` (`:47`) or `ResearchFollowupAction` (`:212`); the inverted default excludes both.
- `tokenpal/actions/research/fetch_url.py` — no declaration needed; the inverted default already excludes it. Listed so the worker does not add one. Arbitrary network fetch.
- `tokenpal/actions/read_file.py` — no declaration needed; the inverted default already excludes it. Listed so the worker does not add one. Reads file contents.
- `tokenpal/actions/grep_codebase.py` — no declaration needed; the inverted default already excludes it. Listed so the worker does not add one. Reads file contents; this is the clause that removes #74's no-user-turn reachability.
- `tokenpal/actions/find_files.py` — no declaration needed; the inverted default already excludes it. Listed so the worker does not add one. Filesystem enumeration.
- `tokenpal/actions/list_processes.py` — no declaration needed; the inverted default already excludes it. Listed so the worker does not add one. Enumerates running processes.
- `tokenpal/actions/timer.py` — no declaration needed; the inverted default already excludes it. Listed so the worker does not add one. Starts a timer the user did not ask for.
- `tokenpal/actions/focus/pomodoro.py` — no declaration needed; the inverted default excludes it.
- `tokenpal/brain/agent.py` — replace the `_PERSISTENT_SINKS` frozenset (`:396`) with `action.writes_durable_sink` at BOTH of its consumers: the execution refusal at `:228` and the spec filter at `:305`. Its comment states the constraint as "Gated on BOTH sides: dropped from the advertised specs, and refused at execution so a sink called in the same batch as the read — or re-emitted from a name the model saw earlier — cannot slip through." Both sides must survive; this is a two-sided gate, not a spec filter. Note `:305` filters spec dicts by name — but no registry lookup is needed: `AgentRunner` already holds `self._actions: dict[str, AbstractAction]` (`agent.py:112,129`), so `self._actions[name].writes_durable_sink` compiles with no import and no cycle. Use `.get()`, because `tool_specs` is an independent constructor kwarg and a spec name need not be present in `_actions`.
- `tokenpal/actions/focus/logs.py` — `writes_durable_sink = True` AND `allow_unprompted = True` on both `habit_streak` and `mood_check`, which share this module. They stay ambient-eligible; only their `_PERSISTENT_SINKS` membership is converted.
- `tests/test_actions/test_reminder.py` — this is where the `_PERSISTENT_SINKS` behavior is actually asserted, NOT `tests/test_agent.py`, which holds zero references to it. Three assertions break by construction: `:526` compares the frozenset to a literal, and `:537-538` use `inspect.getsource(AgentRunner.run)` / `inspect.getsource(AgentRunner._tools_for)` to assert the literal string `"_PERSISTENT_SINKS"` appears in the source. Rewrite all three against `writes_durable_sink` — keeping the two-sided intent: the flag is read at both the execution refusal and the spec filter.
- `docs/claude/actions.md` — `:7` documents the `reminder` name check and `_PERSISTENT_SINKS` as current mechanisms. Both stop existing in this phase, so the doc goes stale HERE, not at p4. Update the two sentences; the fuller author checklist still lands in p4.
- `tests/test_brain/test_tool_loop.py` — the existing ambient assertions (`test_ambient_comments_never_offer_a_tool_that_would_prompt`, `test_ambient_generation_passes_the_narrowed_spec_list`) break under the inverted default and must be UPDATED, not merely re-run: `_EchoAction` (`tests/test_brain/test_tool_loop.py:372-377`) declares no flag, so `assert ambient == ["echo"]` at `:757` and `:774` becomes `[]`. Every test fixture that expects to appear on the ambient path must declare `allow_unprompted = True` — this is the first place the inverted default bites, and it bites in the fixtures rather than the product code. Then add three: a tool leaving `allow_unprompted` at its default is absent from `_build_ambient_specs` and present in `_build_conversation_specs`; `reminder` is still excluded with the name check gone; and **an expected-set test that accounts for every registered action**. Pinning only the 26 catches a forgotten opt-in but NOT a newly added tool, which is simply absent and leaves the equality passing. So pin both halves: `set(ambient_names) == EXPECTED_AMBIENT` and `set(_ACTION_REGISTRY) - EXPECTED_AMBIENT == EXPECTED_EXCLUDED`. A 40th tool then falls into neither set and fails, forcing a deliberate decision in both directions. That test is the review gate the inverted default needs — the declaration on each tool stays the source of truth, and the test only pins today's answer.

## Decisions & findings
### Decision: declare suitability rather than infer it from `requires_confirm`  *(status: active)*
- **Rationale:** `_build_ambient_specs` is a denylist with three mechanical exclusions, so a tool is offered unprompted unless it happens to trip one. Measured against the live registry: **35 of 39 registered actions are ambient-eligible today**, including `fetch_url`, `research`, `read_file`, `grep_codebase`, `find_files`, `list_processes`, `timer` and `pomodoro`. Nobody chose those; they qualify by not being excluded.
- **Alternatives considered:** an explicit allowlist like `M3_CATALOG`'s nine names, which fails closed for future tools and is the stronger design — rejected by the operator as a behavior change across all 35 at once, and because it needs an initial list decided rather than a set of exclusions. Recorded here because it is the natural successor if the exclusion list keeps growing.
- **Evidence:** `tokenpal/brain/orchestrator.py:2033-2050`; the 35-of-39 count from walking `_ACTION_REGISTRY` with the filter's own predicate; `M3_CATALOG` at `tokenpal/brain/idle_tools_m3.py:34`.

### Decision: the hardcoded `reminder` check is the proof, not an exception  *(status: active)*
- **Rationale:** the filter already carries one name-checked exclusion with its own justification at `orchestrator.py:2042-2043` — someone hit this problem for exactly one tool and patched the instance. A second such tool would mean a second name check. Declaring it moves the reason to the tool that owns it.
- **Evidence:** `tokenpal/brain/orchestrator.py:2042-2043` and `:2049`.

## Decisions & findings — shipped at `3bbc46d`

### Finding: the ambient gate is ADVERTISE-ONLY  *(material for p2)*
`Brain._execute_tool_call` (`orchestrator.py:1924`) resolves `self._actions.get(tc.name)` — the full enabled set — and the `gather` at `:1858` passes only `response.tool_calls`. Nothing ties execution to the spec list that was offered. Proven by probe: with `_build_ambient_specs()` returning `[]`, a mock LLM emitting a call to the unadvertised action still executed it. So `allow_unprompted` narrows what the model is SHOWN, not what it can RUN, and a local model that re-emits a name it saw earlier bypasses it. The durable-sink gate beside it is deliberately two-sided for exactly this reason (`agent.py:293-300`). Execution-side enforcement is NOT in this plan's approved scope — parked, see the master.

### Decision: `habit_streak` and `mood_check` are not ambient-eligible  *(operator, 2026-09-05)*
- **Rationale:** their rows land in `habit_log`/`mood_log`, swept by neither `_prune` (`memory.py:923-936`) nor `clear_conversation_summaries` (`:491-496`), and the `name` argument carries no sensitive-term filter — only a 64-char cap. `execute` defaults `should_log=True`, so even a query writes. The reason `reminder` is excluded applies verbatim.
- **Evidence:** `tokenpal/actions/focus/logs.py:98-101`, `:150-153`.

### Decision: `hydration_log` is not a durable sink  *(operator, 2026-09-05)*
- **Rationale:** its argument is a number, not model-authored text, so nothing from a desktop-content read can be laundered through it. The row survives `/clear` like the others; the gate is about text, not about writing. This reasoning was unwritten before and is now a comment at `logs.py:47-49`.

### Finding: the durable-sink lookup fails CLOSED
`_writes_durable_sink` returns True for a name the runner does not hold. The first implementation returned False; a reviewer proved the test comment claiming to cover that case was inert (monkeypatching the fail-closed variant left the test green). Advertising a sink on a lookup miss is the wrong default for a privacy gate.

### Finding: `M1_RULES` and `M3_CATALOG` read no flag
They pick unprompted tools by hardcoded name. All nine M3 names and every M1 `tool_name` currently sit inside the eligible set **by coincidence**. `test_the_idle_rollers_only_fire_ambient_eligible_tools` now pins that.

### Environment findings
- The venv is **Python 3.14** (`.venv/lib/python3.14/`), though CLAUDE.md says "Python 3.12+". No impact here; a later phase touching typing syntax should know.
- `ruff check tests/` has **37 pre-existing failures** (long lines, `N802` on Qt overrides, an unused local). CLAUDE.md's gate is `ruff check tokenpal/`, which passes. Not introduced by this plan.
- `_gated_free_specs` is reset per run at `agent.py:166`, so the cache cannot leak between agent runs.

## Failure modes to anticipate
- **`_REMINDER_TOOL` has a second consumer.** Deleting the constant breaks `orchestrator.py:660`. Delete the filter clause only.
- **The two ambient tests assert on spec *lists*, not on the filter predicate.** Read them before editing; an assertion counting specs will move when the exclusion list lands, and that is a real edit, not a failure.
- **A half-synced venv makes the expected-set test fail confusingly.** `registry.py:45-48` swallows `ImportError` at DEBUG, so without `aiohttp`/`psutil` only 21 of 39 actions register and the set assertion fails with a diff that names missing tools rather than a missing package. Both are core deps (`pyproject.toml:11,19`), so it fails loudly rather than silently — but say so in the assertion message.
- **The inverted default fails silently in the other direction.** Forgetting `allow_unprompted = True` on one of the 26 quietly removes a tool the user has today. Nothing but the expected-set test catches it; write that test first.
- **`writes_durable_sink` has a two-sided gate and a name-keyed consumer.** `agent.py:305` filters spec dicts by name, not by class, so the conversion needs a registry lookup there. Missing that side silently re-opens the laundering path `e1693ec` closed.
- **A tool setting the flag is still offered in conversation.** `_build_conversation_specs` (`:2026`) must not gain the clause — a user asking for research by name must still get it. Assert both directions.
- **`_tool_specs` is cached at `orchestrator.py:379` and rebuilt at `:1749`.** Confirm the ambient list is built per tick (`:1721`) and not served from that cache.

## Done criteria
- `_build_ambient_specs`' output excludes every tool declaring `allow_unprompted = False`, and includes it in `_build_conversation_specs`' output — asserted in `tests/test_brain/test_tool_loop.py`.
- `grep -n "_REMINDER_TOOL" tokenpal/brain/orchestrator.py` returns the definition and `:660` only, and `grep -rn "_PERSISTENT_SINKS" tokenpal/ tests/` returns nothing.
- Observable: the ambient-eligible count is exactly **26**, down from 35, and the set equals the 26 names in Work — printed by walking `_ACTION_REGISTRY` with the filter's predicate (inline in the comprehension at `orchestrator.py:2046-2049`, so it must be retyped) and recorded in this shard's findings.
- `habit_streak` and `mood_check` are still ambient-eligible, and still refused on a desktop-content agent run — the two-sided gate, re-proven in `tests/test_actions/test_reminder.py` against the new flag.
- `pytest tests/test_brain/test_tool_loop.py` green; full suite green.
