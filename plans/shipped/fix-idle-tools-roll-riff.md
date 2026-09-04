# Fix /idle_tools roll riff (#62)

## Status
**Approval: APPROVED 2026-09-04**
**Authored at: 5457a9b**
Research done inline this session (all claims below carry `file:line` at 5457a9b; no
investigator dispatched because every question was answered by opening the source).
Verification pass 2026-09-04 — 34/34 grounding claims resolve · 4/4 done criteria
covered · coherence: 1 minor · 3 fixed (roll-branch range `:1532-1571` → `:1532-1574`;
`run_coroutine_threadsafe` cite `:1566` → `:1567`; inclusions under-stated callers →
now names loop, M3, and roll) · 1 tightened (test 2 offered a choice → stub `_riff`
outright) · 0 refuted · 0 uncheckable.
Worker (opus) shipped the phase 2026-09-04; suite 2224 passed, ruff clean, mypy zero.
**Spec check** — 5/5 Work items evidenced · none unclaimed.
Code shipped as 6a0d7dd. Live check 2026-09-04 on the Mac: `/idle_tools roll coffee_break`
posted a word-of-the-day + trivia riff (log 11:58:08). The silent running-bit branch has no
live rule to exercise it (every running-bit rule sets an opener); covered by the unit test.
Operator declined a follow-up for the mid-sentence token-cap truncation in idle-tool riffs.
SHIPPED.
Simplify pass 2026-09-04 — 5 applied (dead `_ExplodingLLM` stub dropped; `_RecordingPersonality`
replaced by the real `PersonalityEngine` + `active_running_bits()`; `_fake_riff` hoisted to
`_capture_riff`; five `IdleFireResult` literals collapsed to `_fire(**overrides)`; running-bit
assert collapsed to one) · 2 parked (see Parking lot).

## Goal
`/idle_tools roll <rule>` produces an in-character line again, via the same post-fire
path the automatic idle roll uses, and stops carrying its own copy of the idle-tool
context so future context changes land in one place.

## Scope contract
- **Requested outcome:** `/idle_tools roll <rule>` no longer raises `'Brain' object has no
  attribute '_generate_tool_riff'`; the roll delivers the fired result exactly the way
  the brain loop does; mypy reports zero errors.
- **Named semantic boundary:** the post-fire delivery step of the idle-tool path
  (register running bit → silent record or riff) and the idle-tool context constructor.
- **Explicit inclusions:** extract the post-fire block of
  `IdleToolRunner._maybe_fire_deterministic` into one public runner method used by the
  loop, the M3 path, and the roll; replace the roll's hand-built `build_context(...)` call with
  `IdleToolRunner.build_context()`; a runner test for the running-bit/no-opener path;
  sweep the stale `_generate_tool_riff` name in `docs/idle-tool-rolls.md` and
  `tokenpal/brain/idle_tools_m3.py`.
- **Explicit exclusions:** `force_fire` semantics (still bypasses predicates and
  cooldowns, still records the fire); the M3 roller's decision logic; any change to
  what the riff prompt says.
- **Intent class:** bounded outcome.

## Diagnosis
- **Hypothesis:** commit `9cefbc3` moved `Brain._generate_tool_riff` to
  `IdleToolRunner._riff` (`tokenpal/brain/idle_runner.py:148`) and the slash-command
  call site at `tokenpal/app.py:1561` was not swept.
- **Falsifiable test:** `.venv/bin/mypy tokenpal/ --ignore-missing-imports`; and
  `grep -rn "_generate_tool_riff" tokenpal/` returns only the app.py call and a
  docstring.
- **Test result (this session):**
  ```
  tokenpal/app.py:1561: error: "Brain" has no attribute "_generate_tool_riff"  [attr-defined]
  Found 1 error in 1 file (checked 253 source files)
  ```
  `git log -S_generate_tool_riff` → `9cefbc3 brain: extract IdleToolRunner from
  orchestrator`. Confirmed.

## Non-goals
- Not changing `IdleToolRoller.force_fire` (`tokenpal/brain/idle_tools.py:247-267`).
- Not changing how `_riff` builds its prompt or filters output
  (`tokenpal/brain/idle_runner.py:148-210`).
- Not adding a test that drives the `/idle_tools roll` closure in `app.py`; it is a
  nested closure inside `_cmd_idle_tools` (`tokenpal/app.py:1459`) with no existing
  harness, and mypy is the regression guard for the attribute itself.
- Not touching `tests/test_brain/test_idle_tools_roller.py`; its `force_fire` tests
  (`:211`, `:223`) cover the unchanged half of the roll.

## Work
- Scope trace: DIRECT — every bullet serves the requested outcome; the M3 call-site
  change is SAFETY (see Decisions: one delivery path).
- `tokenpal/brain/idle_runner.py` — add a public `deliver(self, snapshot: str, fire:
  IdleFireResult) -> None` *(proposed name)* containing the body currently at
  `_maybe_fire_deterministic` after the `if result is None: return` (the running-bit
  register, the silent `record_fire(..., emitted=True)` return when
  `opener_framing` is empty, and the `_riff` call; today at `:97-105`).
  `_maybe_fire_deterministic` and `_maybe_fire_llm_initiated` (`:132`) both call
  `await self.deliver(snapshot, result)` in place of their current tails. `_riff` and
  `_register_running_bit` stay private and unchanged.
  Shape (proposal, transcribed from `:97-105`):
  ```python
  async def deliver(self, snapshot: str, fire: IdleFireResult) -> None:
      """Register a running bit if any, then riff or record silently."""
      if fire.running_bit:
          self._register_running_bit(fire)
          if not fire.opener_framing:
              self.record_fire(fire, emitted=True)
              return
      await self._riff(snapshot, fire)
  ```
- `tokenpal/app.py` — in the `roll` branch of `_cmd_idle_tools` (`:1532-1574`):
  replace the hand-built `build_context(...)` (`:1546-1554`) with
  `ctx = brain._idle_runner.build_context()` (public, `idle_runner.py:49`), and
  replace `await brain._generate_tool_riff(brain._context.snapshot(), result)`
  (`:1561`) with `await brain._idle_runner.deliver(brain._context.snapshot(), result)`.
  Drop the now-unused local imports `datetime`, `build_context`, `Category`,
  `has_consent` from `_cmd_idle_tools` (`:1460-1464`); grep shows no other use inside
  that function (`grep -n "build_context\|has_consent\|datetime" tokenpal/app.py`
  → the only hits inside `_cmd_idle_tools` are the roll branch).
- `tests/test_brain/test_orchestrator_idle_path.py` — add two async tests using the
  existing `_bare_brain()` fixture (`:15-27`), giving the stub a `_personality` with
  a recording `add_running_bit` and an `_llm` whose `generate` raises if called:
  1. running-bit fire with empty `opener_framing` → `add_running_bit` called once
     with `tag=fire.rule_name`, `generate` never called, `record_fire` telemetry
     path reached (memory is None so it is a no-op; assert no exception).
  2. one-shot fire (`running_bit=False`) → `add_running_bit` not called and the
     riff path is entered: patch `_riff` on the runner instance with a recording
     stub and assert it was awaited once with `(snapshot, fire)`.
- `docs/idle-tool-rolls.md` — `:41` rename `_generate_tool_riff(fire)` to
  `IdleToolRunner.deliver(snapshot, fire)` and fold `:42` (running-bit branch) into
  the same line since `deliver` now owns both branches; `:438` rename
  `_generate_tool_riff` to `IdleToolRunner.deliver`.
- `tokenpal/brain/idle_tools_m3.py` — `:6` docstring: `_generate_tool_riff` →
  `IdleToolRunner.deliver`.
- Added at review (SAFETY / user-visible feedback, applied under the phase-cycle
  review rule): `deliver` and `_riff` return `bool` (True iff a line reached the user);
  the roll branch checks `check_sensitive_app` before `force_fire`, posts a chat note
  when `deliver` returns False, and cancels the future on the 30s timeout so a late
  riff cannot land after the failure message. `docs/idle-tool-rolls.md:280` describes
  the new roll behaviour.

## Decisions & findings
### Decision: one delivery path for loop, M3, and roll  *(status: active)*
- **Rationale:** the automatic path registers running bits and skips the LLM when a
  running-bit rule has no opener framing (`idle_runner.py:97-105`). Eight rules in
  `tokenpal/brain/idle_rules.py` set `running_bit=True` (`:310, :357, :392, :414,
  :433, :523, :563, :605`). A roll that called `_riff` directly would riff off an
  empty framing and never install the bit, so the manual roll would misreport what
  the automatic path does. Routing M3 through the same method costs nothing today
  (`grep -n running_bit tokenpal/brain/idle_tools_m3.py` is empty, so M3 results
  always carry the dataclass default `running_bit=False`,
  `tokenpal/brain/idle_tools.py:75`) and closes the door on a third divergent tail.
- **Alternatives considered:** the issue's one-liner
  `brain._idle_runner._riff(snapshot, result)` — rejected for the running-bit
  divergence above and for reaching into a private method from app.py.
- **Evidence:** cited inline.

### Decision: roll uses `IdleToolRunner.build_context()`  *(status: active; operator-chosen)*
- **Rationale:** operator instruction this session: "replace its hand built context
  with the public function so when we make changes to it we don't need to remember
  to do it twice."
- **Behavioral delta:** none observable. `force_fire` bypasses every predicate
  (`idle_tools.py:250`), and the only consumer of `ctx` on the invoke path is
  `_build_arguments_for_tool` (`idle_tools.py:391-397`), which reads no `ctx` field.
  The runner's context additionally queries memory for streak/install-age/pattern
  callbacks (`idle_runner.py:55-66`); those calls already run every tick on the same
  brain loop the roll coroutine is scheduled onto (`app.py:1567`), so no new thread
  concern.
- **Evidence:** cited inline.

## Failure modes to anticipate
- `_bare_brain()` stubs only what `is_eligible`/`record_fire` need; `deliver` also
  touches `_personality.add_running_bit` and, on the riff path, `_llm`, `_budgets`,
  `_min_tokens`, `_status_callback`, `_push_status`, `_is_near_duplicate`,
  `_emit_comment`, `_recent_outputs` (`idle_runner.py:148-210`). Test 2 should stub
  `_riff` rather than stub all of those; that keeps the test about the branch, not
  the riff internals.
- Dropping the `datetime` import inside `_cmd_idle_tools`: confirm no other branch
  of that function uses it before removing (ruff F401/F821 will catch either way).

## Done criteria
- `.venv/bin/mypy tokenpal/ --ignore-missing-imports` reports `Success: no issues
  found` (zero errors, down from one).
- `grep -rn "_generate_tool_riff" tokenpal/ docs/` returns nothing.
- Live check on the Mac with `./run.sh --overlay textual`: `/idle_tools roll
  coffee_break` (or any enabled one-shot rule from `/idle_tools list`) posts a buddy
  line to the chat pane instead of `/idle_tools roll failed: ...`; and rolling one
  running-bit rule with empty opener framing posts the "fired but nothing was said"
  note instead of a buddy line.
- The two new tests run and pass; `pytest` suite green; `ruff check tokenpal/` clean.

## Parking lot
- ADJACENT: `force_fire` burns the rule's cooldown even when delivery later fails
  (`idle_tools.py:252-253` states this is deliberate). Not required here; the
  delivery failure this plan fixes is the only case it was visibly harmful.
  Disposition at ship: dropped, deliberate per the docstring.
- ADJACENT (simplify, altitude): the roll body in `app.py` still mirrors the shape of
  `_maybe_fire_deterministic` (build context → fire → None check → deliver) with
  `force_fire` swapped in. An `IdleToolRunner.force_fire(rule_name) -> bool` (~8 lines)
  would collapse the slash command to one public call and keep "what a forced roll does"
  next to the automatic path. Deferred: changes the plan's approved interface; needs
  operator sign-off as a follow-up. Disposition at ship: dropped at operator's request
  (no follow-ups filed).
- ADJACENT (simplify, altitude): `brain._loop` + `run_coroutine_threadsafe` reach-in at
  five `app.py` sites (`:609, :1189, :1554, :2689, :2698` at 5457a9b); a
  `Brain.run_coroutine(coro, timeout)` wrapper would centralize it. Pre-existing, touches
  five unrelated commands, out of proportion here. Disposition at ship: dropped at
  operator's request (no follow-ups filed).
