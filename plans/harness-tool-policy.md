# Harness tool policy

## Phase map
**Phase p1 — Tools declare whether they may run unprompted**
- Enters when: start here
- Done signal: `_build_ambient_specs` reads a declared flag and the hardcoded `_REMINDER_TOOL` name check is gone — see the shard
- If it fails: no gate — fix-forward
- Shard: `plans/harness-tool-policy-p1.md`

**Phase p2 — One dispatch point (gate)**
- Enters when: p1 shipped
- Done signal: every LLM tool call reaches `execute()` through `ToolInvoker.invoke` — see the shard
- If it fails: stop. p3 enforces inside `invoke`; without p2 the chat and ambient paths would keep bypassing it
- Shard: `plans/harness-tool-policy-p2.md`

**Phase p3 — Path policy declared, enforced in the invoker**
- Enters when: p2 shipped
- Done signal: `read_file`, `grep_codebase` and `open_path` declare a path policy and make no `resolve_inside` / `allowed_roots` call of their own — see the shard
- If it fails: stop. p4's contract test asserts the surface p3 creates
- Shard: `plans/harness-tool-policy-p3.md`

**Phase p4 — Close #74's second half, then fail closed**
- Enters when: p3 shipped
- Done signal: `grep_codebase` screens each hit, and a contract test fails any tool declaring a path parameter without a policy — see the shard
- If it fails: no gate — fix-forward
- Shard: `plans/harness-tool-policy-p4.md`

## Status & cold-start
**Approval: APPROVED 2026-09-05**
**Authored at: 087d5a4**
**research: investigator · specialists: security boundary, concurrency or ordering**

Verification pass 2026-09-05 — 47/47 claims resolve · 0 unchecked · 4 phases covered · coherence 7 contradictions, all fixed · 30 fixed · 2 promoted · 2 refuted.
Named fixes: `path_screen` selected one predicate for both stages (would have made `read_file` return `docs/credentials.md`, undoing 087d5a4) → raw screen only, resolved always `path_is_sensitive(rel)`; then that raw screen was unconditional (verified to newly refuse `<root>/credentials-app/README.md` on `open_path`) → `"narrow"` means no raw screen. Resolver returned `(resolved, rel)` → a `ResolvedPath` NamedTuple with `root`, without which `open_path`'s kept `is_hidden_or_protected(resolved, root)` would not compile and `read_file`'s dual-spelling check would be dead code. `tuple[...] | str` error case → `| None` (verified `a, b = "no"` unpacks). `git_root` memo → deleted (coroutine reuse, two monkeypatch surfaces, 25 `monkeypatch.chdir` tests). Relative anchor unpinned → per roots policy. p2's Done grep would have matched `memory.py`'s 60+ `self._conn.execute(` → keyed on dispatch shape. p4's precedent anchors `:328`/`:337` → `:332`/`:377-385`. p4's behavioural assertion ran against `execute()`, which p3 empties → routed through `invoke`. p4's selector exact-matched names → substring. Timings re-measured (`allowed_roots` cannot be cheaper than the `git_root` it awaits).
Refuted: "the chat path bypasses `ToolInvoker`, so p3 leaves it unprotected" — true at HEAD, but p2 routes `orchestrator.py:1942`; the phase map already gates on it. Two auditors reached it independently reading p3 alone, so p3 gained an explicit inherited-precondition check rather than a rebuttal. "The reorder means a refusal no longer burns a rate-limit slot" — unobservable; no path tool declares a `rate_limit`.
design: systems 9 checked · 5 fixed · integration 8 checked · 5 fixed · re-audit of the rewritten p3: systems 4 blocking + 3 minor, security 1 blocking + 2 significant + 3 minor — all fixed or refuted above.
personas: security boundary 8 checked · 5 fixed · concurrency 8 checked · 3 fixed.
history: 1 qualifying (`tokenpal/brain/orchestrator.py`, 136 commits) · 1 mined · 0 re-litigation · 2 eroded rationale · 1 re-introduction pattern · 2 promoted.
Re-audit of the rewritten p1 (fail-closed default + `writes_durable_sink`), 2026-09-05: grounding/executability 9 findings, integration 9 findings, 4 blocking between them, all fixed. The 26-name opt-in set was verified set-identical in both directions against the live registry by both auditors independently — 0 errors. Named fixes: the `_PERSISTENT_SINKS` tests are in `tests/test_actions/test_reminder.py`, not `tests/test_agent.py` which holds zero references, and two of them assert the literal string via `inspect.getsource`; `_EchoAction` (`test_tool_loop.py:372-377`) declares no flag so the two ambient assertions BREAK rather than pass under the inverted default; the expected-set test pinned only the 26 and was blind to a newly added tool, now pins both halves; `agent.py:305` needs no registry lookup because `AgentRunner` already holds `self._actions`; `docs/claude/actions.md:7` goes stale at p1, not p4.
**Approval: APPROVED 2026-09-05** — operator signed off the ten-tool exclusion list, uncapped chat after seeing the before/after code shape, converting `_PERSISTENT_SINKS` to a declaration, and the fail-closed default. The non-conflation of `writes_durable_sink` from `allow_unprompted` was a correction made at approval and is flagged in the response.

**p1 SHIPPED at `3bbc46d`. p2 SHIPPED at `38d9d68`.** NEXT is p3 — read `plans/harness-tool-policy-p3.md` FIRST.

**Spec check at p1** — 10/10 Work items evidenced · none unclaimed (the 21 action modules are the opt-ins Work described by grep recipe).
**Spec check at p2** — 4/4 Work items evidenced · 1 unclaimed (`tests/test_brain/test_followup_handler.py`, a `Brain.__new__` fixture; required by the described work, added to Work, planning miss recorded — p1 hit the same pattern in `test_reminder.py`).
**Sweep at p2** — opened p3 and p4. p3 gains the `Brain.__new__` fixture warning, which is material because p3 reads action attributes inside `invoke`. p4 clean.
**Sweep at p1** — opened p2, p3, p4. p2 gains the advertise-only finding as context and a note that `_execute_tool_call` is unchanged by p1. p3 clean — its `base.py` ClassVars are additive to p1's two. p4 gains a note that `docs/claude/actions.md` was already partly updated by p1.

Binding decisions for p3, pulled inline:
- The invoker substitutes a `ResolvedPath` NamedTuple (`raw`, `resolved`, `root`, `rel`) into a COPY of `kwargs` — never a bare string, which would make `read_file`'s `_spelled_rel` return exactly `rel` and kill the untracked-symlink defence.
- `path_screen` governs the RAW-name screen only; the resolved name is always screened with `path_is_sensitive(rel)`. `"narrow"` means no raw screen — screening a raw absolute path newly refuses benign files under a badly-named folder.
- Containment runs BEFORE the rate-limit block, which must stay await-free.
- No `git_root` memoization: it breaks the monkeypatch surfaces and 25 `monkeypatch.chdir` tests.

Binding decisions for p1, pulled inline so a compaction still leaves them visible:
- `allow_unprompted` defaults to **`False`** — fail closed (operator, 2026-09-05). A new tool is ambient-ineligible until a human opts it in. The 26 opt-ins land in the same commit, and an expected-set test makes an omission fail loudly.
- `_PERSISTENT_SINKS` converts to its own ClassVar, `writes_durable_sink`, NOT into `allow_unprompted`. Different policy: it fires only on a desktop-content run and means "writes to a durable local sink". They overlap on `reminder` alone; merging would drop `habit_streak`/`mood_check` from ambient and lose the quarantine CLAUDE.md requires.
- `requires_confirm` and `reads_desktop_content` stay in the ambient filter. `allow_unprompted` is additive, not a replacement — the modal-stall and prompt-only reasons are independent of suitability.

## Goal
Make tool policy something a tool declares and the harness enforces at one place, instead of something each tool implements by hand. Concretely: close issue #74 structurally, and stop the buddy from being offered 35 of its 39 tools on unattended ambient ticks.

## Scope contract
- **Requested outcome:** tool policy — unprompted suitability and filesystem path containment — is declared per tool as metadata and enforced centrally, so a future tool is contained and ambient-safe by default rather than by author discipline. Structurally closes #74.
- **Named semantic boundary:** the tool dispatch boundary — every code path from an LLM tool call to `AbstractAction.execute()` — plus the per-tool policy metadata declared on `AbstractAction`.
- **Explicit inclusions:** `allow_unprompted`, `path_params`, `path_roots` and a per-tool path screen strength as ClassVars; the ambient spec filter; routing the chat and ambient dispatcher through `ToolInvoker`; removing hand-rolled containment from `read_file`, `grep_codebase` and `open_path`; an output-side per-hit screen for `grep_codebase`; a fail-closed contract test; author docs.
- **Explicit exclusions:** content sniffing for secrets inside otherwise-allowed files; issue #27 tool subsetting; changing which tools the idle rollers (`M1_RULES`, `M3_CATALOG`) may run; any LLM or ML classifier; a user-configurable ambient list.
- **Intent class:** bounded consolidation

## Non-goals
- **No LLM or ML classifier anywhere in this plan.** Containment is decidable — `resolve()` then `is_relative_to` — and a probabilistic check would fail open. Operator's position, confirmed 2026-09-05.
- **Detecting secrets by content.** `path_is_sensitive` and `REJECT_PATH` match on names, so a credential inside `notes.txt` stays invisible. Parked with evidence; entropy/pattern detection, not an LLM.
- **Changing what the idle rollers may run.** `M1_RULES` and `M3_CATALOG` stay the deciding mechanism on those paths. Safe to exclude: their tool sets do not intersect any of the eleven tools this plan touches — `{read_file, grep_codebase, find_files, open_path, reminder, timer, pomodoro, list_processes, research, research_followup, fetch_url}` — verified this session by walking `M1_RULES` (`tool_name` plus `extra_tool_names`) and `M3_CATALOG` against the live registry; both intersections are empty. Both catalogs hold the same nine flavor tools.
- **Moving confirmation into the invoker.** `agent.py:341-344` wraps `invoke` in `asyncio.wait_for(..., 60s)`; a modal inside that timeout is cancelled, `app.py:275`'s `if not fut.done()` guard then drops the user's answer, and the dialog stays on screen accepting clicks that do nothing.
- **Moving the desktop-content session flag.** `agent.py:316-321` sets `session.desktop_content` before the first trace line so a denied confirm still fails closed; it is a property of the session, not of the call.
- **A user-configurable ambient list.** `allow_unprompted` is author-declared. A `config.toml` surface for it is parked.
- **Making `safe` enforced.** It is read only by `app.py:1357` for `--check` display. Named here so a reader does not mistake it for part of this consolidation.

## Files touched
- `tokenpal/actions/base.py` — P1, P3 — the ClassVars: `allow_unprompted` (default False) and `writes_durable_sink` in P1, `path_params`/`path_roots`/`path_screen` in P3
- `tokenpal/brain/agent.py` — P1 — `_PERSISTENT_SINKS` becomes `writes_durable_sink`, both sides of its gate (`:228` execution refusal, `:305` spec filter) preserved
- `tokenpal/actions/focus/logs.py` — P1 — `habit_streak` and `mood_check` declare the flag; they share this module
- `tests/test_actions/test_reminder.py` — P1 — the three `_PERSISTENT_SINKS` assertions, two of them `inspect.getsource` string checks, move to the new flag
- `tokenpal/brain/orchestrator.py` — P1, P2 — ambient filter reads the flag (P1); chat/ambient dispatcher routes through an invoker (P2)
- `tokenpal/actions/invoker.py` — P2, P3 — `enforce_rate_limit` kwarg (P2); path resolution and substitution (P3)
- `tokenpal/util/paths.py` — P3 — shared declared-path resolver, memoized `git_root`
- `tokenpal/actions/read_file.py` — P1, P3 — declares `allow_unprompted = False`; local containment deleted in P3
- `tokenpal/actions/grep_codebase.py` — P1, P3, P4 — flag; declares a path policy; gains a per-hit output screen in P4
- `tokenpal/actions/open_path.py` — P3 — declares a path policy, local containment deleted
- `tokenpal/actions/find_files.py` — P1, P3 — flag; shares the roots computation only, output-side filter stays
- `tokenpal/actions/research/research_action.py` — P1 — `allow_unprompted = False` on both actions
- `tokenpal/actions/research/fetch_url.py` — P1 — `allow_unprompted = False`
- `tokenpal/actions/reminder.py` — P1 — `allow_unprompted = False`, replacing the name check deleted from the filter
- `tokenpal/actions/timer.py` — P1 — `allow_unprompted = False`
- `tokenpal/actions/list_processes.py` — P1 — `allow_unprompted = False`
- `tokenpal/actions/focus/pomodoro.py` — P1 — `allow_unprompted = False`
- `tests/_helpers.py` — P3 — `stub_allowed_root` follows `load_config` out of the action modules
- `tests/test_brain/test_tool_loop.py` — P1, P2 — ambient spec assertions; dispatcher rework
- `tests/test_brain/test_followup_handler.py` — P2 — its `Brain.__new__` fixture needs the new `_chat_invoker`; added during execution
- `tests/test_invoker.py` — P2, P3 — `enforce_rate_limit`; path resolution
- `tests/test_actions/test_read_file.py` — P3 — refusal strings move to the shared layer
- `tests/test_actions/test_grep_codebase.py` — P3, P4 — containment, then per-hit screening
- `tests/test_actions/test_open_path.py` — P3 — containment moves, confirm ordering unchanged
- `tests/test_actions/test_find_files.py` — P3 — follows `stub_allowed_root` to its new owner
- `tests/test_util/test_paths.py` — P3 — the shared resolver and its refusal shapes
- `tests/test_actions/test_path_policy_contract.py` — P4 — new, the fail-closed contract test
- `CLAUDE.md` — P4 — the `allowed_dirs` overstatement, and the new author rule
- `docs/claude/actions.md` — P1, P4 — `:7`'s `reminder` / `_PERSISTENT_SINKS` mechanisms go stale at P1; the author checklist for the path policy lands at P4

## Background findings
- **Six code paths reach `AbstractAction.execute()`.** Four go through `ToolInvoker.invoke` (`agent.py:342`, `idle_tools.py:379` and `:403`, `idle_tools_m3.py:193`). Two do not: `orchestrator.py:1942` and `orchestrator.py:2635`. Enumeration closed by grepping `action.execute` and the nine `_actions[`/`_actions.get(` lookup sites.
- **`_execute_tool_call` serves typed chat AND the ambient observation tick**, via `_reply_with_continuation` (`orchestrator.py:1893`) and `_generate_comment` (`:1717`). "The chat path" is really "the conversation and ambient path".
- **The chat path is the only concurrent dispatcher** — `asyncio.gather` at `orchestrator.py:1858-1860`, up to `_MAX_TOOL_ROUNDS = 8`. The agent path is deliberately sequential (`agent.py:222-224`).
- **The rate-limit block is atomic only because it contains no `await`.** Demonstrated: an `await` between the length check and `q.append` turns `[True, True, False, False, False]` into `[True, True, True, True, True]` under `gather`. Any awaiting work added to `invoke` goes strictly before or after that block.
- **`contains_sensitive_term` and `path_is_sensitive` are not nested in either direction.** Broad-only: `chase`, `fidelity`, `health`, `calm`, `messages`, `signal`, `headspace`. Narrow-only: `docs/credentials.md`, `.env`, `a.pem`, `sub/id_rsa` — all four are `False` under `contains_sensitive_term`. `read_file` applies BOTH (raw: `REJECT_PATH` or `contains_sensitive_term`; resolved: `path_is_sensitive`); `grep_codebase` applies the broad one to the raw argument only; `find_files`/`open_path` apply the narrow one. So the screen is additive, not a choice between predicates.
- **`find_files` declares no path argument.** Its containment is output-side, in `_post_filter` (`find_files.py:211-242`). `path_params` is structurally inapplicable; only the `allowed_roots(load_config().paths.allowed_dirs)` computation is shareable.
- **`read_file`'s git-tracking check is membership, not containment** (`read_file.py:88-92`, both spellings). No roots declaration can express it; it stays tool-local.
- **`grep_codebase` passes its target to a subprocess argv**, not `open()` (`grep_codebase.py:74`). Validating a path is not enough — the resolved value must be substituted into the argv.
- **Hoisting relocates a subprocess rather than adding one.** All four path tools already call `git_root` or `allowed_roots` inside `execute()`. Re-measured (the first pass reported `allowed_roots` cheaper than the `git_root` it awaits, which cannot be true): `load_config()` 0.73 ms, `git_root` 4.63 ms, `allowed_roots` 5.11 ms, neither cached. Gate the work on a non-empty `path_params` or it lands on every tool call.
- **Neither `git_root` nor `load_config()` is cached, and this plan adds no cache.** `os.chdir` appears nowhere in `tokenpal/`, so `Path.cwd()` is a process constant — but the tests are not: 25 `monkeypatch.chdir` calls across `test_read_file.py`, `test_git_actions.py` and `test_grep_codebase.py` drive these tools, and `git_root` is monkeypatched at `tests/_helpers.py:244` and five places in `tests/test_util/test_paths.py`. A process-lifetime memo would defeat both. `load_config()` is separately uncacheable because `/options` rewrites `config.toml` at runtime.
- **`allowed_roots([""])` and `allowed_roots(["/nonexistent"])` both re-enable the tools** with the repo root, because the empty-list short circuit only fires on a genuinely empty sequence (`paths.py:39-64`). CLAUDE.md's "an explicitly empty list disables them" is true only for `[]`.
- **`test_privacy_contract.py` has three fail-open modes**, two from #65 item 1 (`pytest.skip` on any constructor raise at `:242-246`; `_imported_modules` returning an empty set at `:71-80`) and a third found this session: an empty `parametrize` list is a **skip, not a failure** — demonstrated. P4's test must differ deliberately on all three.
- **`memory.tool_usage_counts` has no production caller** (`memory.py:1361`; only `tests/test_brain/test_memory.py`). The `on_call` hook currently feeds a write-only table — relevant to whether P2 wires `on_call` for chat.

## Done criteria
- A tool that declares nothing is absent from `_build_ambient_specs`' output; exactly 26 named tools appear; the hardcoded `_REMINDER_TOOL` check and the `_PERSISTENT_SINKS` frozenset no longer exist.
- Every LLM tool call reaches `execute()` through `ToolInvoker.invoke`; a test fails if a new direct dispatch appears in `tokenpal/brain/`. The assertion must match the dispatch shape, NOT the bare word `.execute(` — `tokenpal/brain/memory.py` calls `self._conn.execute(` more than sixty times.
- `grep_codebase` with an absolute path outside the repo is refused, and with no path argument no longer returns the contents of an in-repo `credentials.md` or `id_rsa`.
- `read_file`, `grep_codebase` and `open_path` contain no `resolve_inside` call of their own.
- A new action declaring a schema property named `path` and no `path_params` fails the suite.
- The full suite is green and `test_privacy_contract.py` still passes unchanged.

## Parking lot
- **Content-based secret detection** — `path_is_sensitive` matches names only, so a credential inside `notes.txt` is invisible to every layer this plan adds. Evidence: P4's per-hit screen filters `grep_codebase` hits by filename, not by matched text. Useful, but a different mechanism (entropy/pattern detection) and explicitly out of scope per the operator.
- **A user-configurable ambient list** — `[tools] ambient_tools` or an `/options` surface, so the operator can exclude a tool without editing source. `allow_unprompted` is author-declared. Not required for the requested outcome.
- **`open_path`'s existence oracle** — it checks `exists()` (`open_path.py:93`) before `path_is_sensitive` (`:100`), so `allowed/credentials.md` answers "That path is protected." while `allowed/nope_credentials.md` answers "No such file.", distinguishing a denied name that exists from one that does not. P3 adopts `read_file`'s deny-before-stat order for the containment step, which narrows but does not remove this; the remaining ordering inside `open_path` is ADJACENT.
- **`test_privacy_contract.py` could gain `assert cls.allow_unprompted is False`** for every `reads_desktop_content` tool — a marked tool should never be ambient-eligible. Cheap and genuinely invariant, but p4 requires that file stay unchanged, and adding it is scope this plan was not asked for. One-line follow-up.
- **Execution-side ambient enforcement.** p1 established that the ambient gate is advertise-only: `_execute_tool_call` runs any name the model emits, so `allow_unprompted` is not enforced at execution the way `writes_durable_sink` is. Closing it means threading ambient context into the dispatcher. **Out of this plan's approved scope** — the operator approved four phases before this was known. Worth an issue.
- **`memory.tool_usage_counts` is dead** — a write-only table with no production reader. Unrelated to this boundary.
- **A cancelled `/idle_tools roll` still consumes a rate-limit slot** — `app.py:1567` cancels after 30 s, but `invoker.py:44` has already appended. Pre-existing, unaffected by this plan.
