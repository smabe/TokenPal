# harness-tool-policy-p2 — One dispatch point

You are phase `p2` of the `harness-tool-policy` plan. This phase delivers, as one commit, the routing of the chat and ambient dispatcher through `ToolInvoker`, so that every LLM tool call reaches `execute()` through one place. It adds no new policy — p3 is what puts containment inside that place.

## Locked decisions
See the master `plans/harness-tool-policy.md`. The decisions binding this phase:
- **Confirmation does not move.** `agent.py:341-344` wraps `invoke` in `asyncio.wait_for(..., 60s)`. A modal inside that timeout is cancelled; `app.py:275`'s `if not fut.done()` then drops the user's answer, the dialog stays on screen, and clicking Allow does nothing. Confirm stays in the caller, before `invoke`.
- **The desktop-content session flag does not move.** `agent.py:316-321` sets it before the first trace line so a denied confirm still fails closed.
- **Chat gets its own Brain-lifetime invoker, not the agent's.** `_build_invoker()` (`orchestrator.py:2219-2228`) is called per `/agent` run so rate-limit state resets between goals (`invoker.py:5-6`). Sharing one instance would silently delete that property.
- **Chat and ambient do not enforce rate limits.** Operator's stated preference, 2026-09-05, on the understanding that p1 removes `research` from the ambient path entirely. Implemented as an `enforce_rate_limit` kwarg on `ToolInvoker`, defaulting `True` so `/agent` and both idle rollers are unchanged. **Signed off by the operator 2026-09-05, after seeing the before/after code shape**, on the understanding that p1 removes `research` from the ambient path entirely, so the remaining uncapped surface is typed chat where the user asked for it and is present. **If the operator instead wants chat capped:** drop the kwarg entirely, construct `self._chat_invoker = ToolInvoker()`, and the Done observable inverts — the third `research` call in 120 s refuses from chat as well. No other Work changes.
- **`on_call` stays unwired for the chat invoker.** Wiring it would start writing every chat and ambient tool call into `memory.db`'s `tool_calls` table via a synchronous sqlite commit on the brain loop (`memory.py:1350-1359`), and that table's only reader (`memory.tool_usage_counts`, `:1361`) has no production caller.

## Inherited from p1 (shipped `3bbc46d`)
`AbstractAction` now carries `allow_unprompted` and `writes_durable_sink`; `_build_ambient_specs` reads the former through `Brain._is_ambient_eligible`. **`_execute_tool_call` itself is unchanged by p1** — the line this phase rewrites is exactly where p1 left it. Note p1 found that the ambient gate is advertise-only precisely because this dispatcher resolves names against the full enabled set; that is parked, not this phase's to fix, and this phase must not quietly widen into it.

## Work
- Scope trace: PREREQUISITE — p3 enforces containment inside `ToolInvoker.invoke`. Without this phase the chat and ambient path would keep calling `execute()` directly and would be exempt from every policy p3 adds.
- `tokenpal/actions/invoker.py` — add the kwarg. Proposed (shape is contract; name and default are proposals):
  ```python
  def __init__(self, on_call: CallRecord | None = None, *,
               enforce_rate_limit: bool = True) -> None:
      ...
      self._enforce_rate_limit = enforce_rate_limit

  # in invoke():
  limit = action.rate_limit if self._enforce_rate_limit else None
  ```
  Everything below that line is unchanged. The check-and-append block stays synchronous and await-free.
- `tokenpal/brain/orchestrator.py` — construct `self._chat_invoker = ToolInvoker(enforce_rate_limit=False)` in `Brain.__init__`, beside `self._confirm_lock` (`:426`). In `_execute_tool_call` (`:1923-1958`) replace `await action.execute(**tc.arguments)` (`:1942`) with `await self._chat_invoker.invoke(action, tc.arguments)`. Everything above `:1942` — unknown-name, desktop-content refusal, the confirm block under `_confirm_lock` — is untouched. `_handle_followup` (`:2635`) routes the same way.
- `tests/test_brain/test_followup_handler.py` — **added to Work during execution.** `_bare_brain_with_action` builds a `Brain` via `Brain.__new__` and hand-sets six attributes without running `__init__`, so routing `_handle_followup` made two tests raise `AttributeError: no attribute '_chat_invoker'`. Planning miss: the Work list predicted the `__new__` fixture problem for `test_reminder.py` in p1 but not for this file, and the same pattern is used in both.
- `tests/test_invoker.py` — add: `enforce_rate_limit=False` skips the limit and still calls through; the default still enforces.
- `tests/test_brain/test_tool_loop.py` — the file pins this dispatcher closely (unknown tool, `"Error: {e}"` on exception, `gather` parallelism, `_MAX_TOOL_ROUNDS`, and the five confirm tests). All must stay green. Add a characterization test that no `action.execute(` call remains in `tokenpal/brain/` — a source-level assertion, so a sixth dispatch site added later fails the suite.

## Decisions & findings
### Decision: the chat invoker is instance state on `Brain`, constructed in `__init__`  *(status: active)*
- **Rationale:** it needs Brain lifetime, and it must not be a module global. An `asyncio.Lock` created at module scope binds to the first loop that hits its contended path and then fails cross-loop under `pytest asyncio_mode=auto` — demonstrated. The same reasoning applies to any shared object here; `_confirm_lock` (`orchestrator.py:426`) is the local precedent.
- **Alternatives considered:** reusing `_build_invoker()` — rejected, it would share rate-limit state with `/agent` and wire `on_call`. A per-turn invoker — rejected, `_MAX_TOOL_ROUNDS = 8` bounds a turn anyway, so a per-turn limit is unenforceable by construction.
- **Evidence:** `tokenpal/brain/orchestrator.py:426`, `:2084`, `:2219-2228`; `tokenpal/actions/invoker.py:5-6`.

### Decision: the source-level "no direct execute" test is the phase's real deliverable  *(status: active)*
- **Rationale:** routing two call sites is a five-line change that any later commit can silently undo. The guarantee this phase claims — one dispatch point — is only durable if a new direct call fails the suite.
- **Evidence:** the six-path enumeration in the master's Background findings.

### Finding: the rate-limit observable, run as a unit test  *(status: active)*
Stub action `_Limited` (`tests/test_invoker.py`), `RateLimit(max_calls=2, window_s=120.0)`, no backend and no network. Three invokes each way:

```
enforce_rate_limit=False: ['1', '2', '3'] action body ran 3 times
default             : ['1', '2', 'rate limit: 2 calls per 120s exceeded'] action body ran 2 times
```

Pinned by `test_enforce_rate_limit_false_skips_the_limit` and
`test_default_still_enforces_the_limit`.

### Finding: only two actions in the registry declare a `rate_limit`  *(status: active)*
`research` (2 per 120 s, `research_action.py:69`) and `research_followup`
(5 per 120 s, `:237`). Both are chat-reachable, so `enforce_rate_limit=False`
is the entire behavioural surface of the kwarg on this path — and it preserves
HEAD exactly, since the direct `execute()` calls it replaces consulted no
limit either. This phase therefore changes no runtime behaviour at all.

### Finding: `tests/test_brain/test_followup_handler.py` builds Brains with `Brain.__new__`  *(status: active)*
`_bare_brain_with_action` hand-sets six attributes and never runs `__init__`,
so `_chat_invoker` was missing and two tests raised `AttributeError` at the
newly routed `_handle_followup` line. Fixed by setting
`brain._chat_invoker = ToolInvoker(enforce_rate_limit=False)` in that helper.
The `_ScriptedAction` double is not an `AbstractAction` and has no
`rate_limit` attribute; the conditional expression short-circuits, so it never
reads one. `## Work` did not name this file — an unavoidable edit.

## Decisions & findings — shipped at `38d9d68`

### Finding: the first dispatch guard was defeatable, and that was the phase's whole deliverable
The initial detector flagged a `.execute(` call whose receiver was *named* `action`/`tool` or which unpacked `**`. Review demonstrated five bypasses — `impl.execute(question=q)`, `handler.execute(question=q)`, `self._registry[n].execute(question=q)`, `fn = action.execute; fn(**args)`, `getattr(a, "execute")(**args)` — the first of which is the `_handle_followup` site under a rename. It also false-flagged `self._transaction.execute(sql)` and `self._conn.execute(sql, **binds)`, both plausible in a 61-call sqlite file. Replaced with a pinned receiver SET over all of `tokenpal/` (`invoker.py` exempt): every legitimate receiver today is sqlite — `conn`, `cursor`, `self._conn` — and any new one fails. Verified red by injecting `impl.execute(question='x')` into `orchestrator.py` and green on restore.

### Finding: this phase changed no runtime behaviour
Verified: with `enforce_rate_limit=False`, `limit` short-circuits to `None` so `_call_times` is never written (`{}` after 50 invokes); `_on_call` is `None` so the hook block is inert; exceptions propagate identically and `CancelledError` remains a `BaseException`, so `except Exception` catches what it caught. Nothing per-call is stored on the invoker, so one shared instance under the 8-way `gather` is safe.

### Refuted: "`_chat_invoker` serves only chat"
A reviewer claimed both readers are typed chat and that ambient builds its own invoker. False. `_generate_with_tools` has two callers: `orchestrator.py:1897` (typed chat) and `:1721`, inside `_generate_comment` — the ambient observation tick, passing `tool_specs=self._build_ambient_specs()`. Both reach `_execute_tool_call`. The reviewer confused the ambient TICK with the M1/M3 idle ROLLERS, which do hold their own invokers (`idle_tools.py:138`, `idle_tools_m3.py:114`). The comment's justification was still wrong and was fixed: ambient is unattended, so "the user typed it" only holds because p1 keeps the rate-limited research pair off that half.

### Refuted: "reuse `_build_invoker()` instead of a second construction site"
It would wire `on_call → memory.record_tool_call`, sending every chat and ambient tool call to `memory.db`'s `tool_calls` table. This shard's Locked decisions settled that deliberately; it is a behaviour change, not a cleanup.

### Finding: 11 `Brain.__new__` fixtures exist, and p3 will hit more of them
`test_qt_overlay.py:354`, `test_nudge_emission.py:88`, `test_orchestrator_idle_path.py:21`, `test_orchestrator_mood_callback.py:16`, `test_desktop_tasks.py:273`, `test_orchestrator_afk.py:33`, `test_near_duplicate_guard.py:28`, `test_followup_handler.py:30,77,117`, `test_suppression_cooldown.py:34`. Only one needed patching here, because only one reaches a dispatch site. **p3 adds work inside `invoke` that reads attributes off the action**, so any fixture reaching a tool path will need a real action rather than a duck type. `_ScriptedAction` was converted to a genuine `AbstractAction` subclass for this reason.

## Failure modes to anticipate
- **Return-type mismatch.** `_execute_tool_call` returns `str` and consumes `display_text` / `display_url` / `display_urls` before flattening to `result.output`; `invoke` returns `ActionResult`. Mechanical, but the `except Exception` at `:1956` currently wraps `execute`; keep it wrapping `invoke`.
- **`gather` concurrency.** Up to eight `_execute_tool_call` coroutines share one invoker instance. Nothing may be stored on `self` per call. The rate-limit block is correct today only because it contains no `await`.
- **The confirm tests are load-bearing.** `test_chat_confirm_prompts_never_overlap` pins that `_confirm_lock` covers the modal and is released before execution — so N gathered non-confirm tools still run in parallel while one confirm is pending. Do not widen the lock around `invoke`.
- **`_handle_followup` is a slash command, not an LLM tool call.** It passes a fixed `question=` argument. Routing it is for uniformity; if it fights the `ActionResult` unwrapping at `:2635`, stop and report rather than reshaping that handler.

## Done criteria
- No LLM tool dispatch in `tokenpal/brain/` calls `execute()` directly. The assertion must key on the dispatch shape — `action.execute(` / `.execute(**` against an `AbstractAction` — and must NOT match `.execute(` generally, which `tokenpal/brain/memory.py` hits sixty-plus times on a sqlite connection.
- A test asserts that source fact, so a new direct dispatch site fails the suite.
- `ToolInvoker(enforce_rate_limit=False)` skips the limit; the default enforces. Both asserted in `tests/test_invoker.py`.
- Observable, run as a unit test with a stub action declaring `RateLimit(max_calls=2, window_s=120.0)` — no live backend, no network: three invokes through a `ToolInvoker(enforce_rate_limit=False)` all reach the action, and three through a default `ToolInvoker()` return the third as `"rate limit: 2 calls per 120s exceeded"`. Paste both outputs into this shard's findings. (A live `research` comparison needs a running buddy, an LLM and network, and is dogfood, not a gate.)
- `pytest tests/test_brain/test_tool_loop.py tests/test_invoker.py tests/test_agent.py` green; full suite green.
