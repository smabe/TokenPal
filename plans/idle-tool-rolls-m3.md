# Idle Tool Rolls — M3: LLM-initiated tool calls

**Status:** proposed — approval pending
**Issue:** [#33](https://github.com/smabe/TokenPal/issues/33)
**Ships on top of:** M1 + M2 ([`plans/idle-tool-rolls.md`](./idle-tool-rolls.md), shipped 2026-04-17)
**Arch doc for M1+M2:** [`docs/idle-tool-rolls.md`](../docs/idle-tool-rolls.md)

## Problem

M1 + M2 give us **deterministic** idle tool-calling. Predicates say "it's
Monday morning, first session of the day" and weighted-random picks one
of the matching rules. That's excellent for time-of-day rituals. It is
*not* good for the middle ground:

- User is 40 minutes into a normal Tuesday coding session. No predicate
  says "look up today's word now" — they already heard it at 8am. No
  predicate says "throw out a random fact" — `long_focus_fact` fires
  only when Deep-focus is active.
- The LLM, given the freedom, might know from context that a weather
  callback, a trivia question, or a follow-up on a running bit would
  actually land. We don't have a predicate for "this feels right."

M3 closes that gap: during a freeform tick, optionally give the LLM a
flavor-tool catalog and let it decide whether calling one would make the
next line better. Tool call → tool result → in-character riff, all in
one tick.

## Goals

- **G1** The LLM can choose to call a flavor tool during freeform when
  context suggests it would land.
- **G2** Neither over-used nor under-used. Target: LLM-initiated fires
  are 10-30% of freeform ticks when eligible. Tuning knob, not a hard
  target.
- **G3** No new privacy exposure. Same consent gate, same sensitive-app
  bypass, same tool catalog subset we curate ourselves.
- **G4** No increase in user-visible comment rate. M3 replaces the
  freeform line it would have emitted — it does not add a new one.
- **G5** Clean telemetry so we can tell deterministic (M1+M2) from
  LLM-initiated (M3) fires and compare quality.

## Non-goals

- **NG1** Multi-step agent behavior during idle. One tool call, one
  riff, one emission. That's it. /agent stays its own path.
- **NG2** Exposing side-effectful tools (open_app, timer, research).
  Flavor subset only — see tool catalog below.
- **NG3** Replacing M1 or M2 rules. Deterministic rolls stay as the
  high-confidence floor; M3 fills the middle ground.
- **NG4** LLM-initiated calls during observation ticks. Observations
  are gated on sense changes and have their own signal — tools would
  muddy that. M3 only rides on the freeform path.

## Preconditions for shipping

This plan is deliberately written to be approved *later*, after M2 has
baked. Before we cut code for M3.1 we need:

1. **M2 dogfood ≥ 2 weeks.** We need to hear whether the 8h
   `morning_word` running bit actually fires in subsequent prompts —
   if the LLM ignores the bit, M3 is probably premature too.
2. **Telemetry baseline.** Count `idle_tool_fire` rows per day across
   deterministic rules. If the deterministic floor is already hitting
   3-4 fires a day, M3 adds value on top; if it's averaging 0-1, we
   should tune M2 cooldowns before adding LLM slack.
3. **Base-model posture.** Run a 30-minute test with
   `generate_with_tools` on Qwen3-14B-Q4_K_M and gemma4 with the
   flavor-subset catalog attached. If either model reliably picks the
   same tool every time (template lock-in) we need a newer base model
   or a rewritten system prompt before M3 ships.
4. **Near-duplicate guard survives LLM output.** Commit `dd4535b` was
   validated against template drift on hand-written riffs. We need
   one real dogfood week where the LLM riffs on M2 tool output and
   the guard catches the near-duplicates the same way.

## Design overview

```
┌───────── brain loop tick ────────────────────────────────────┐
│ poll senses → context → gate decision                        │
│                                                              │
│  gate="comment":   → _generate_comment (unchanged)           │
│  gate="freeform":  → NEW: _generate_freeform_comment_m3      │
│  gate="silence":   → IdleToolRoller.maybe_fire (M1+M2)       │
│                                                              │
│  _generate_freeform_comment_m3:                              │
│    1. Is M3 eligible? (config + rate cap + circuit breaker   │
│       + mood gate + last-LLM-fire cooldown)                  │
│    2. If NOT eligible → old freeform path. Done.             │
│    3. If eligible → build freeform prompt + catalog + call   │
│       self._llm.generate_with_tools(messages, tools)         │
│    4. Response has tool_calls? → invoke the first one,       │
│       feed the result back into the LLM with the second      │
│       turn, generate riff. Emit riff.                        │
│    5. Response has text and no tool_calls? → emit the        │
│       text as a normal freeform line. No penalty — model     │
│       declined to use a tool, which is a legitimate choice.  │
│    6. Response has neither? → fall back to old path.         │
│                                                              │
│  Telemetry: every fire writes idle_tool_fire with            │
│    source="llm_initiated" + reason="freeform_tick"           │
└──────────────────────────────────────────────────────────────┘
```

Key architectural decisions:

- **Single tick, no multi-step loop.** Unlike `/agent`, which can make
  up to `max_steps` calls, M3 is capped at one tool call per freeform
  tick. The second LLM turn just turns the tool result into prose —
  it cannot issue another tool call. This prevents "I called trivia,
  now I'll call random_fact to compare" spirals.
- **Reuses `generate_with_tools`.** Already exists on `AbstractLLMBackend`
  and is exercised by `/agent` and `/ask`, so the wire format works.
- **Shares telemetry schema with M1+M2.** One `idle_tool_fire` row per
  fire, new `source` field distinguishes the origin.
- **Shares cooldowns with M1+M2.** When a deterministic rule fires
  `joke_of_the_day`, the M3 path treats that tool as cooled-down for
  the rule's `cooldown_s`. Prevents same-day duplicates across paths.

## Data model changes

### `IdleToolsConfig` — new fields

```python
@dataclass
class IdleToolsConfig:
    # existing M1/M2 fields…
    enabled: bool = True
    global_cooldown_s: float = 600.0
    max_per_hour: int = 4
    rules: dict[str, bool] = field(default_factory=dict)

    # NEW for M3
    llm_initiated_enabled: bool = False          # default OFF until baked
    llm_initiated_cooldown_s: float = 1800.0     # 30min min-gap (issue #33)
    llm_initiated_max_per_hour: int = 1          # paranoid cap
    llm_initiated_mood_block: tuple[str, ...] = ("focused", "sleepy")
    llm_initiated_consecutive_tool_block: int = 3  # circuit breaker trip count
    llm_initiated_consecutive_tool_cooldown_s: float = 7200.0  # 2h cool-off
```

Defaults are deliberately conservative: LLM-initiated OFF by default on
first install. Gets flipped on by the user via
`/idle_tools llm_on` once M3 has shipped.

### `IdleFireResult` — add `source`

```python
@dataclass
class IdleFireResult:
    # existing fields…
    source: Literal["deterministic", "llm_initiated"] = "deterministic"
```

Telemetry consumers ignore unknown values; this field is additive.

### New: `LLMInitiatedTracker`

Mirrors `IdleToolRoller`'s per-rule state but keyed by **tool name**,
not rule name, because M3 doesn't use rules.

```python
@dataclass
class LLMInitiatedTracker:
    last_fire_any: float | None = None
    recent_fires: deque[float] = field(default_factory=deque)
    # Rolling record of which tool the LLM picked each time, keyed
    # chronologically. Used to detect "called trivia 3x in a row" drift.
    recent_tool_choices: deque[str] = field(default_factory=deque)
    # Per-tool cool-off windows installed by the circuit breaker. Shared
    # with the deterministic path: a deterministic fire of tool X also
    # writes `last_fire_by_tool[X] = now`.
    last_fire_by_tool: dict[str, float] = field(default_factory=dict)
```

Lives on `IdleToolRoller` (composition, not inheritance) so the M1+M2
fire path can bump its state without knowing M3 is watching.

## Tool catalog (curated subset)

The LLM never sees the full action registry — too many side effects, too
easy to pick `research` or `open_app` by mistake. M3 defines an
explicit flavor subset in `tokenpal/brain/idle_tools_m3.py`:

| Tool | Gate | Why included |
|---|---|---|
| `word_of_the_day` | web | Running-bit adjacent. LLM may callback. |
| `joke_of_the_day` | web | Same. |
| `on_this_day` | web | History hook. |
| `moon_phase` | web | Evening flavor. |
| `random_fact` | web | Tangent fodder. |
| `trivia_question` | web | Interactive hook. |
| `weather_forecast_week` | web | Contextual. |
| `sunrise_sunset` | web | Dawn/dusk flavor. |
| `memory_query` | offline | Consent-free floor + callback. |

All tool definitions come from `ActionRegistry` — we reuse the
`.parameters` JSON schema each action already exposes. Wrapping:

```python
def _m3_tool_spec(action: AbstractAction) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": action.action_name,
            "description": action.description,
            "parameters": action.parameters,
        },
    }
```

Excluded explicitly: `timer`, `open_app`, `do_math`, `system_info`,
`list_processes`, `research`, `search_web`, `fetch_url`, `read_file`,
`grep_codebase`, `git_*`. These are either side-effectful, privacy
sensitive, or too heavyweight for an idle quip.

## Prompt shape

The freeform prompt grows one section between `{running_bits_block}`
and `{recent_comments_block}`:

```
Optional tools you may call (call at most one, only if it would
genuinely improve your next line. Calling zero tools is the right
answer most of the time):
- word_of_the_day: returns today's dictionary word
- joke_of_the_day: returns one short joke
- on_this_day: returns one historical item for today's date
- moon_phase: returns the current lunar phase
- random_fact: returns one bite-sized factoid
- trivia_question: returns one trivia question (no answer revealed)
- weather_forecast_week: returns this week's local forecast
- sunrise_sunset: returns today's sunrise and sunset times
- memory_query: local-only; returns one habit stat about the user

Rules:
1. If you decide not to call a tool, just respond in character. That
   is the normal outcome. Do not apologize for not calling a tool.
2. Never call two tools in one turn.
3. Never call a tool whose output would repeat something you already
   said in the last 30 minutes.
```

The hard rule against announcing tool use is the anti-drift guardrail:
without it, quantized models tend to say *"let me check the moon
phase"* as prose, then emit the tool call, producing a visible
artifact in the riff.

## Rate limiting strategy

Four independent checks, all of which must pass:

1. **Master enable.** `idle_tools.llm_initiated_enabled`.
2. **Global LLM-initiated cooldown.** `now - tracker.last_fire_any <
   config.llm_initiated_cooldown_s` → bail.
3. **Rolling-hour LLM-initiated cap.** Same logic as M1+M2, but only
   counts LLM-initiated fires, capped at `llm_initiated_max_per_hour`.
4. **Mood gate.** `personality.mood in config.llm_initiated_mood_block`
   → bail. Default blocks `focused` and `sleepy`.
5. **Per-tool cooldown (shared with M1+M2).** `now - tracker.last_fire_by_tool[T]
   < per_tool_cooldown_s(T)` → remove T from the catalog this tick. If
   the catalog empties, bail.

Per-tool cooldowns default to the deterministic rule's cooldown for
the same tool (e.g., `morning_word.cooldown_s = 18h` applies equally
to LLM-initiated `word_of_the_day` calls). Tools without a
deterministic rule (`random_fact`, `trivia_question` already have
rules; edge cases added later) get a conservative 2h default.

### Circuit breaker (issue #33 line-item)

If the same tool appears in `tracker.recent_tool_choices` N times in a
row (`llm_initiated_consecutive_tool_block`, default 3), install a
`llm_initiated_consecutive_tool_cooldown_s` (default 2h) override
that blocks *both* the LLM-initiated path and the deterministic path
from firing that tool. Resets as soon as the LLM picks a different
tool.

Implementation: one `dict[str, float]` of per-tool cool-off
timestamps on the tracker; checked in step 5 above.

## Fallback behavior

Every failure mode below falls through to the original freeform path
(`self._llm.generate(prompt)` with no tools). User sees a normal
line; debug log captures why M3 didn't fire.

- Backend doesn't support tool calls (older Ollama): caught by the
  `generate_with_tools` base-class fallback, which already calls
  `generate`. Nothing to do.
- Model returns text without a tool call: emit the text. This is a
  successful outcome per G2 — model chose no-tool. No telemetry row.
- Model returns a tool call for a tool not in our M3 catalog: reject
  and fall through. Log at DEBUG. This catches prompt drift where
  the model invents a tool name.
- Tool invocation fails: emit the no-tool text the model also
  generated (LLM tool responses usually include a message too). If
  there's no text either, fall through.
- Second LLM turn (riff generation) fails or returns empty after
  `filter_response`: record an `emitted=False` telemetry row; do not
  retry.

## File-level changes

### NEW

- `tokenpal/brain/idle_tools_m3.py` — catalog + `LLMInitiatedTracker`
  + `maybe_llm_initiated()` method that packages the freeform prompt,
  calls `generate_with_tools`, invokes the chosen tool, and returns
  an `IdleFireResult`.
- `tests/test_brain/test_idle_tools_m3.py` — mock LLM backend returns
  pre-canned tool-call responses; assert that invocation fires, rate
  caps hold, circuit breaker trips, and fallback paths are taken.

### EDIT

- `tokenpal/brain/idle_tools.py` — expose shared state
  (`last_fire_by_tool`) to M3; add `source` field to `IdleFireResult`;
  bump shared cooldowns on every deterministic fire.
- `tokenpal/brain/orchestrator.py` — split
  `_generate_freeform_comment` into a pre-step that asks the M3
  tracker if it wants to take over. When M3 fires, emit through the
  existing `_generate_tool_riff` path with `fire.source = "llm_initiated"`.
- `tokenpal/config/schema.py` — new `IdleToolsConfig` fields listed
  above.
- `config.default.toml` — annotated stubs for each new field, all OFF
  by default until M3 ships.
- `tokenpal/app.py` — `/idle_tools` gains two subcommands:
  `llm_on`/`llm_off` to flip `llm_initiated_enabled` and `llm_status`
  to dump the tracker (last fire, rolling-hour count, per-tool
  cooldowns, circuit-breaker state).
- `tokenpal/config/idle_tools_writer.py` — `set_llm_initiated_enabled`
  mutator mirror of `set_idle_tools_enabled`.
- `docs/idle-tool-rolls.md` — new "LLM-initiated path (M3)" section
  describing the tracker, catalog subset, rate limits, and circuit
  breaker. Keep M1+M2 sections intact.
- `CLAUDE.md` — update the one-line pointer to mention M3 is live.

## Tests

- `test_idle_tools_m3.py`
  - `test_disabled_llm_path_falls_through` — default-off config means
    the M3 tracker never fires and the normal freeform path runs.
  - `test_tool_call_emitted_when_model_picks_one` — mock
    `generate_with_tools` returns a tool_call for `word_of_the_day`;
    assert the action was invoked and the riff prompt included the
    tool output.
  - `test_text_only_response_falls_through_silently` — model returns
    text with no tool_calls; assert freeform text is emitted normally,
    no telemetry row.
  - `test_out_of_catalog_tool_rejected` — model picks `open_app`;
    assert fallback to text-only generation.
  - `test_llm_initiated_rate_cap` — fire twice; second call blocked by
    `llm_initiated_max_per_hour=1`.
  - `test_llm_initiated_cooldown` — single fire, next tick within
    30min blocked by `llm_initiated_cooldown_s`.
  - `test_mood_gate_blocks_focused` — mood = focused → M3 bails.
  - `test_circuit_breaker_trips_on_consecutive_picks` — three
    consecutive `trivia_question` picks → per-tool cool-off installed
    and both LLM + deterministic paths blocked for 2h.
  - `test_shared_cooldown_with_deterministic_path` — deterministic
    `evening_moon` fires `moon_phase` → M3 cannot fire `moon_phase`
    within the rule's `cooldown_s`.
  - `test_second_turn_failure_records_emitted_false` — tool succeeds,
    riff LLM call throws → telemetry row with `emitted=False`.

- Extend `test_orchestrator_idle_path.py`
  - `test_freeform_tick_routes_to_m3_when_eligible` — pre-populated
    config + mocked LLM returning tool_call; assert orchestrator
    routes through M3 path and emits via `_generate_tool_riff`.

## Sequencing

- **M3.0** — ship telemetry `source` field, `/idle_tools llm_status`
  command (mostly read-only), and a minimum-viable off-by-default
  config surface. Zero behavior change. ~1 day.
- **M3.1** — land the catalog + tracker + `maybe_llm_initiated` call
  path. Still off by default. Add the test matrix above. ~3 days.
- **M3.2** — flip `llm_initiated_enabled` behind a self-gated env var
  (`TOKENPAL_M3=1`) for author dogfood. Tune prompt + catalog based
  on real output over ~1 week.
- **M3.3** — ship per-tool circuit breaker, cross-path cooldown, mood
  gating. Write the `docs/idle-tool-rolls.md` M3 section. ~2 days.
- **M3.4** — flip default to available-but-still-off. Add a first-run
  wizard nudge. User opts in via `/idle_tools llm_on`. Bake for
  ~2 weeks before closing issue #33.

## Open decisions (for approval)

1. **Default `llm_initiated_enabled`.** Plan ships it OFF. Alternative:
   ship ON for rich-voice users only (`has_rich_voice = True`), since
   those users already get elevated `_FREEFORM_CHANCE_RICH`. Verdict
   pending.
2. **Catalog size.** 9 tools as listed, or tighter (5: word, joke,
   fact, trivia, memory_query)? Larger catalogs increase chance of
   one-tool drift. Verdict pending dogfood.
3. **Mood-gating default set.** Currently `focused` + `sleepy`. Should
   `concerned` (2-5am) also block? Probably yes — late-night is
   solidarity mode, not trivia mode. Leaning yes, need confirmation.
4. **Second-turn riff: separate call or single turn?** Current design
   does two LLM calls (tool-choice + riff). Alternative is a single
   call with the tool result injected as a system message mid-turn.
   Two calls is cleaner; single-call is faster but harder to debug.
   Ship two, revisit if latency is visible.
5. **Do we let M3 refresh M2 running bits?** E.g., if the LLM picks
   `word_of_the_day` at 2pm, should the resulting word become a
   running bit like M1's `morning_word` does? Gut says no — M3 is for
   one-shot flavor, not long-running narrative threads. Running bits
   stay a deterministic-only concept. Flag in the plan for review.

## Success metrics / kill criteria

**Ship criterion:**
- M2 has baked ≥ 14 days with ≥ 1 deterministic fire/day average.
- `generate_with_tools` smoke test passes on both supported inference
  engines (Ollama gemma4, llamacpp Qwen3).
- Author opinion after a one-week private dogfood of M3.2: "this is
  better than plain freeform."

**Kill criterion (if observed post-ship):**
- Tool-choice rate < 5% across 7 days of use even when eligible. Model
  never picks a tool. Roll back, revisit with newer base model.
- Tool-choice rate > 60% across 7 days. Model can't resist. Either
  lower `max_per_hour` to 0.5/hr or suspend M3 and wait for a better
  base model.
- Near-duplicate guard trips ≥ 30% of M3 fires. LLM is cycling the
  same three tools with the same framing — means the catalog wasn't
  distinctive enough or the soft rules aren't being honored.

## Known risks

- **Prompt-cache thrash.** Every freeform tick currently reuses the
  same cached prefix up to `{running_bits_block}`. Adding the tool
  catalog splits the cache into M1/M2 vs M3 halves. Acceptable cost;
  tool catalog is static across a session so the M3 prefix is itself
  cacheable.
- **Tool-call format drift.** Ollama sometimes emits tool calls inside
  `content` as pseudo-JSON. `/agent` already handles this via the
  `ToolCall.extract` helpers in `tokenpal/llm/base.py`. Reuse, do not
  reinvent.
- **Consent regressions.** Easy mistake: an LLM picks `trivia_question`
  but user hasn't granted `web_fetches`. Mitigation: the M3 catalog
  step filters by consent before sending the tools list. Verify in
  `test_m3_consent_gate_filters_catalog`.
- **Voice drift.** A snarky voice given a tool catalog might narrate
  the choice ("Let me consult the fact machine"). The prompt rules
  above + existing `filter_response` cleanup cover this but it's
  worth a voice-specific spot-check during M3.2 dogfood.
