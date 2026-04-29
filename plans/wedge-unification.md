# wedge-unification

## Context

Today the Brain's tick (`tokenpal/brain/orchestrator.py:759-810`) is a
hand-written priority cascade plus an idle-tool fallback. Every Wedge has
a different shape:

| Wedge                | Ask shape                                              | Owns gate? | Owns riff?              |
|----------------------|--------------------------------------------------------|------------|-------------------------|
| `RageDetector`       | `ingest(readings) -> Signal \| None`                   | bypass cap | Brain `_generate_rage_check`   |
| `GitNudgeDetector`   | `ingest(readings)` then `check(user_present)`          | bypass cap | Brain `_generate_git_nudge`    |
| `IntentEngine`       | `check_drift()` (internal state)                       | needs cap  | Brain `_generate_drift_nudge`  |
| `IdleToolRoller`     | `maybe_fire(IdleToolContext)`                          | own state  | Brain `_generate_tool_riff`    |
| (anonymous)          | `_should_comment()` + `_should_freeform()` gates       | n/a        | Brain `_generate_comment` / `_generate_freeform_comment` |

Three mismatches:

1. Different ask signatures.
2. Different gate ownership (bypass vs cap-subject vs internal).
3. Five `_generate_*` methods on the Brain, each baking in a wedge-specific
   prompt and call sequence.

Per `CONTEXT.md`, the unifying concept is **Wedge**. The deepening makes
`Wedge` deep: one ABC, one priority resolver, one shared Riff pipeline.

## Goal

Collapse the cascade into a registry of `Wedge`s with a uniform interface,
and a single Riff pipeline. After this lands, adding a new Wedge means
writing one class and registering it - no orchestrator edits, no new
`_generate_*` method.

## Non-goals

- **ProactiveScheduler.** Stays as-is. It is a multi-tenant scheduler with
  its own tick semantics, not a per-tick candidate emitter.
- **Sensitive-app handling.** Stays universal in the Brain, applied once
  before any riff, not per-Wedge.
- **Sense / SenseReading shape.** Untouched.
- **Action / `@register_action`.** Untouched.
- **`_BackendRegistry[B]`.** Wedges are not Backends; they get their own
  registry.
- **LLM call site / TTS / overlay routing.** Reused as-is.
- **New Wedges.** No new behavior in this plan, only the shape change.

## Design

### Wedge ABC

```python
# tokenpal/brain/wedge.py
class GatePolicy(Enum):
    BYPASS_CAP = auto()        # rage, git_nudge, urgent-git
    NEEDS_CAP_OPEN = auto()    # drift, normal comment
    IDLE_FILL = auto()         # freeform, idle_tools, llm_initiated_tool

@dataclass(frozen=True)
class EmissionCandidate:
    wedge_name: str
    payload: object              # opaque; the wedge knows the type

class Wedge(ABC):
    name: ClassVar[str]
    priority: ClassVar[int]      # higher = earlier in tiebreak
    gate: ClassVar[GatePolicy]

    def ingest(self, readings: list[SenseReading]) -> None: ...
    @abstractmethod
    def propose(self, now: float) -> EmissionCandidate | None: ...
    @abstractmethod
    def build_prompt(self, candidate: EmissionCandidate, ctx: PromptContext) -> str: ...
    def on_emitted(self, candidate: EmissionCandidate) -> None: ...
```

Heavy deps (`MemoryStore`, `PersonalityEngine`, `ToolInvoker`,
`ResearchConfig`) are constructor-injected per Wedge. Pull, not push.

### Brain tick (after)

```
readings = await self._poll_all_senses()
for w in self._wedges: w.ingest(readings)

candidates = [(w, c) for w in self._wedges if (c := w.propose(now))]
chosen = self._select_candidate(candidates)
if chosen is not None:
    await self._riff(chosen)
```

`_select_candidate` filters by `GatePolicy` against current state
(`_should_comment`, `_should_freeform`, "any other wedge proposed") and
ranks remaining by `(priority, registration_order)`.

`_riff(wedge, candidate)` is the single shared pipeline:

1. Sensitive-app guard. Today each `_generate_*` runs its own check
   (e.g. `_generate_rage_check:1369`); pulling it into the pipeline is
   intentional deduplication, not new behavior. On block, still call
   `wedge.on_emitted(candidate)` so wedge-internal cooldowns start.
2. `wedge.build_prompt(candidate, ctx)` where `ctx: PromptContext` carries
   `personality`, `budgets`, `min_tokens`, `status_callback`, `snapshot`.
   The exact shape is frozen during Phase 2's rage migration.
3. LLM call.
4. Text guards (`personality.filter_response`, near-duplicate check,
   clean-english, drift filter).
5. `ui_callback` via `_emit_comment`.
6. **Cap-accounting (NEEDS_CAP_OPEN only).** On successful emit, the
   pipeline ticks `_consecutive_comments`, appends to `_comment_timestamps`,
   updates `_last_comment_time`. This state stays on Brain - it's the
   property of "the comment cap," not any one Wedge.
7. `wedge.on_emitted(candidate)` for wedge-internal cooldowns.

### Wedge inventory after

| Wedge name           | priority | gate            | replaces                                |
|----------------------|----------|-----------------|-----------------------------------------|
| `rage`               | 90       | BYPASS_CAP      | `_rage` + `_generate_rage_check`        |
| `git_nudge`          | 85       | BYPASS_CAP      | `_git_nudge` + `_generate_git_nudge`    |
| `drift`              | 50       | NEEDS_CAP_OPEN  | `_intent.check_drift` + `_generate_drift_nudge` |
| `comment`            | 10       | NEEDS_CAP_OPEN  | `_should_comment` + `_generate_comment` |
| `freeform`           | 5        | IDLE_FILL       | `_should_freeform` + `_generate_freeform_comment` |
| `llm_initiated_tool` | 2        | IDLE_FILL       | `_maybe_fire_llm_initiated_tool`        |
| `idle_tool`          | 1        | IDLE_FILL       | `_maybe_fire_idle_tool` + `_generate_tool_riff` |

`has_urgent` (a git transition reading this tick) is a **cap-bypass flag**, not
a separate wedge: it opens the cap for the regular Comment wedge so the
existing observation prompt fires on a git event without dedicating a riff
to it. Phase 4 wires this into `_select_candidate` when CommentWedge
migrates; until then it stays in the cascade.

## Phases

Each phase ends with `pytest` green and a tight commit. Cap stage diffs at
~150 lines per the discipline rules in `CLAUDE.md`. Run /simplify after
each phase, not at the end.

- **Phase 1: ABC + registry, no behavior change.** Add
  `tokenpal/brain/wedge.py` with `Wedge`, `GatePolicy`, `EmissionCandidate`,
  `WedgeRegistry`. Empty registry plumbed into Brain init alongside the
  current cascade. Tests: import smoke, registry round-trip.
- **Phase 2: rage as the first Wedge.** Move `RageDetector` behind the
  `Wedge` interface (`name="rage"`, `gate=BYPASS_CAP`, priority=90). Brain
  calls it via the registry path AND keeps the old cascade path for the
  others. Delete `_generate_rage_check`'s prompt body into
  `RageWedge.build_prompt`. Tests: existing `test_rage_*` pass through the
  new shape; one new test that picks a rage candidate over a comment when
  both are eligible.
- **Phase 3: git_nudge.** Same pattern as rage. `has_urgent` stays in
  the cascade as a cap-bypass flag for now; it is not a wedge of its own
  (it has no dedicated riff).
- **Phase 4: drift, comment, freeform.** Move `IntentEngine.check_drift`
  behind a `DriftWedge`. Split today's `_generate_comment` and
  `_generate_freeform_comment` into `CommentWedge` and `FreeformWedge`.
  Cascade now has only `idle_tools` left.
- **Phase 5: idle_tool + llm_initiated_tool.** Wrap `IdleToolRoller` and
  the M3 LLM-initiated roller as Wedges. The IdleToolContext lives inside
  the Wedge now and is built lazily inside `propose`.
- **Phase 6: delete the cascade.** Remove the if/elif body, all five
  `_generate_*` methods, and `_inject_brain_deps`'s wedge-specific knobs.
  Brain tick is `for w in registry: w.ingest(...); candidate = select(...);
  await riff(candidate)`. Verify: full pytest, manual smoke (run.sh, watch
  one of each wedge fire).

## Tests that survive

- `tests/test_rage_detector.py`, `test_git_nudge.py`, `test_intent.py`,
  `test_idle_tools*.py`: rewritten to assert against the Wedge interface
  (`propose` returns the right candidate; `build_prompt` produces expected
  text). Pure data, no Brain bootup.
- New: `tests/test_brain/test_wedge_select.py` - covers gate policies and
  priority ordering in isolation. One file, replaces six implicit
  cascade-order assertions scattered across orchestrator tests.
- New: `tests/test_brain/test_riff_pipeline.py` - one set covering
  "LLM error -> no emit", "drift filter triggers retry", "clean-english
  filter rejects", "sensitive-app blocks". Survives every future Wedge.
- Deleted: orchestrator tests that asserted the order of the if/elif
  cascade.

## Risks

- **State carry-over.** Rage and git_nudge maintain cross-tick state in
  their `ingest` methods. If we accidentally construct two wedge instances
  the state machines split. Mitigation: registry is single-instance per
  Wedge class; phase 2 has a test that runs five ticks and asserts state
  persisted.
- **Hidden coupling in `_generate_*`.** Each `_generate_*` reaches into
  Brain state (`self._personality`, `self._memory`, `self._context`).
  Mitigation: `PromptContext` dataclass is the new ingress; we collect
  what's needed during phase 2's first migration and freeze the shape.
- **M3 cooldown sharing.** `IdleToolRoller` and the M3 LLM-initiated
  roller share a `FireTracker`. They become two Wedges sharing one tracker
  - clean separation, but easy to break by accident. Mitigation: phase 5
  has an explicit cross-Wedge cooldown test.

## Done criteria

- Brain `run()` body: poll senses, ingest readings into all wedges,
  collect candidates, select one, riff. No `_generate_*` methods left on
  Brain. No if/elif over wedge types in the loop body.
- All existing wedge tests pass through the new interface.
- `test_wedge_select.py` and `test_riff_pipeline.py` exist and are green.
- One manual smoke session: rage fires, git_nudge fires, drift fires,
  idle_tool fires - all in one run if you can engineer it; otherwise
  separate runs with the appropriate gates open.
- LOC delta on `orchestrator.py`: at least -800 (today: 3024).

## Parking lot

- **Wedge plugin discovery.** Today the registry is hand-wired in Brain
  init. If we ever want third-party Wedges, hook into the same
  `pkgutil.walk_packages` machinery used by Senses and Actions. Defer
  until there is a concrete second registrar.
- **Per-Wedge config schema.** Each Wedge today reads its own block of
  `Config` (RageDetectConfig, GitNudgeConfig, IdleToolsConfig). Could be
  unified into `wedges.<name>` once the migration is shipped. Out of
  scope for this plan.
- **Telemetry.** A unified `wedge.fired` event log would replace the
  scattered `log.info` calls in each `_generate_*`. Worth doing but only
  after the shape settles.
