# Idle Tool Rolls

The buddy's brain loop has three emission paths: observation (gated
comment on a sense change), freeform (unprompted in-character thought),
and **idle-tool roll** (contextual tool-flavored riff during quiet
stretches). This doc covers the third.

Read this before editing `tokenpal/brain/idle_tools.py`,
`tokenpal/brain/idle_rules.py`, or the running-bit / idle-roll wiring in
`tokenpal/brain/orchestrator.py`.

## Why it exists

Observation-only idle loops either spam near-duplicate comments (user
stays in one app, the only change is the productivity accumulator
ticking up) or go silent. Neither is fun. Idle-tool rolls fill the
silence with higher-signal flavor the LLM couldn't invent from context
alone — today's word, tonight's moon phase, a historical this-day item,
a trivia question — by invoking a real tool and feeding its output back
into a riff prompt.

## Emission path diagram

```
┌─────── brain loop tick ─────────────────────────────────────┐
│ poll senses → context window → gate decision                │
│                                                             │
│   gate says "comment":    → _generate_comment               │
│   gate says "freeform":   → _generate_freeform_comment      │
│   gate says "silence":    → IdleToolRoller.maybe_fire       │
│                                                             │
│   maybe_fire:                                               │
│     1. enabled? global cooldown? rate cap?                  │
│     2. filter rules by predicate + consent + per-rule cd    │
│     3. weighted-random pick among passing rules             │
│     4. warm-cache lookup or live tool call                  │
│     5. for chain rules, fan out to extra_tool_names         │
│     6. return IdleFireResult | None                         │
│                                                             │
│   Brain then either:                                        │
│     a. one-shot:     _generate_tool_riff(fire)              │
│     b. running-bit:  _register_running_bit(fire) + opener   │
└─────────────────────────────────────────────────────────────┘
```

The roller fires **only when the comment gate chose silence**, so it
cannot inflate the comment rate. Pacing, 8-per-5min cap, near-duplicate
guard, and sensitive-app silence all still apply.

The observation-path **forced-silence window** does NOT apply to idle
rolls — see `Brain._idle_tools_eligible` in `orchestrator.py`. That
window exists to stop near-dup LLM spam; idle rolls inject fresh tool
output and are the right recovery from dead air, not something to
suppress further. Before this decoupling landed a single overnight
session saw zero idle rolls across 11 hours because suppressed
observations kept the silence window re-triggering. See
`commentary-gate.md` for the emission-gate plumbing that sits upstream.

## Rule catalog

All 20 rules live in `tokenpal/brain/idle_rules.py::M1_RULES`. Each is a
frozen `IdleToolRule` dataclass.

| Rule | Tool | Window / predicate | Cooldown | Running-bit? |
|---|---|---|---|---|
| `evening_moon` | `moon_phase` | 21:00–23:59 | 24h | — |
| `morning_word` | `word_of_the_day` | first-session 6–10, morning | 18h | 8h |
| `monday_joke` | `joke_of_the_day` | Mon first-session 6–10 | 7d | — |
| `weather_change` | `weather_forecast_week` | weather reading just changed | 6h | — |
| `long_focus_fact` | `random_fact` | any reading contains "Deep focus" | 2h | 2h |
| `deep_lull_trivia` | `trivia_question` | >15min since last comment, not focused | 2h | — |
| `on_this_day_opener` | `on_this_day` | first-session 6–12 | 18h | 3h |
| `lunar_override` | `moon_phase` | full-moon approx + hour ≥ 22 | 24h | 4h |
| `todays_joke_bit` | `joke_of_the_day` | 11–14 midday lull, settled | 12h | 4h (silent) |
| `morning_monologue` | chain of 3 | first-session 6–9 | 24h | — |
| `memory_recall` | `memory_query` | >15min session + >10min silence | 3h | — |
| `friday_wrap` | chain of 3 | Fri 15–18, settled, >7min silence | 7d | — |
| `coffee_break` | chain of 2 | 10–12, NOT first-session, settled | 12h | — |
| `late_night_host` | chain of 3 | 23:00–01:59, not focused, >10min silence | 24h | — |
| `git_shipped_callback` | `random_fact` | git sense `changed_from` + non-WIP msg | 1h | 2h |
| `streak_celebration` | `trivia_question` | productivity summary contains "focus streak" | 6h | — |
| `callback_streak` | `memory_query` | daily_streak_days >= 3, settled, >7min silence | 6h | 3h |
| `session_arc` | `memory_query` | session_minutes >= 180, >10min silence | 12h | — |
| `habit_rehearsal` | `memory_query` | session_minutes < 10 + first-app pattern cached | 20h | 30min (silent) |
| `anniversary` | `random_fact` | install_age_days in {7, 30, 90, 180, 365} | 24h | — |

**Offline floor:** `memory_recall` is the only rule with
`needs_web_fetches=False`. Every other rule silently drops when the
user hasn't granted the `web_fetches` consent category; `memory_recall`
keeps the feature alive without network access.

**Running bits.** Seven rules register multi-hour callbacks so the
fresh detail can ride along later observations instead of being orphaned
on a single line:

- `morning_word` — 8h decay, announces on first fire ("today's word is…")
- `long_focus_fact` — 2h decay, promoted from one-shot so a fact riffed
  during a deep-focus stretch can weave into later observations
- `on_this_day_opener` — 3h decay, announces one pick and keeps it rideable
- `lunar_override` — 4h decay, keeps the full-moon vibe through the
  late-night window
- `todays_joke_bit` — 4h decay, SILENT (no opener), callback-only
- `git_shipped_callback` — 2h decay, announces a real ship with a
  celebratory random-fact aside
- `callback_streak` — 3h decay, celebrates a 3+ daily-session streak
- `habit_rehearsal` — 30min decay, SILENT, caches a first-app routine
  for the LLM to riff on without announcing it

`PersonalityEngine._running_bits` is a 3-slot LRU. Four of those eight
can register in a morning; the oldest evict first, which is fine
because their `bit_decay_s` was already short for that reason.

**Chain rules:** `morning_monologue` invokes its primary tool
(`weather_forecast_week`) plus every name in `extra_tool_names`
(`sunrise_sunset`, `on_this_day`) and bundles the outputs into a single
riff prompt. If any one extra tool fails, the monologue continues with
the outputs it could gather — graceful degradation instead of silence.

## Hot-path latency

Every network tool used by the idle path has a worst-case HTTP round
trip (hundreds of ms). We can't afford that on the brain tick, so
`IdleToolRoller.warm_daily_cache()` runs once at session start and
pre-fetches the evergreens:

```
_DAILY_EVERGREEN_TOOLS = {
    "word_of_the_day", "joke_of_the_day",
    "on_this_day", "moon_phase", "sunrise_sunset",
}
```

Cache TTL is 6 hours (`_DAILY_CACHE_TTL_S`). On a cache miss inside
`_invoke_single`, the roller refreshes on-demand so a skipped warm-up
doesn't leave rules permanently stuck.

Non-evergreen tools (`weather_forecast_week`, `random_fact`,
`trivia_question`, `memory_query`) make a live call at roll time.
`memory_query` is local-disk; the rest are fast enough in practice.

## Data model

### `IdleToolContext`

Snapshot passed to every predicate. Built once per tick by
`Brain._build_idle_context()` from sense readings + personality state
+ consent.

```python
@dataclass(frozen=True)
class IdleToolContext:
    now: datetime
    session_minutes: int
    first_session_of_day: bool
    active_readings: Mapping[str, SenseReading]
    mood: str
    weather_summary: str
    time_since_last_comment_s: float
    consent_web_fetches: bool
    daily_streak_days: int = 0             # personalization: from MemoryStore
    install_age_days: int = 0              # personalization: from MemoryStore
    pattern_callbacks: tuple[str, ...] = ()  # personalization: from MemoryStore
    @property
    def hour(self) -> int
    @property
    def weekday(self) -> int  # Mon == 0
```

`first_session_of_day` uses a memory-store lookup in
`Brain._compute_first_session_of_day()`. It's True only until the first
emission of the day, not across the whole first session.

**Personalization signals** default to empties so unit-tested predicates
that don't need them can construct an `IdleToolContext` without
plumbing a MemoryStore. `Brain._build_idle_context` populates all three
from the live store each tick; `get_pattern_callbacks` is cached for
the session, the other two are single-query reads.

### `IdleToolRule`

```python
@dataclass(frozen=True)
class IdleToolRule:
    name: str                       # stable id, used in config + logs
    tool_name: str                  # matches AbstractAction.action_name
    description: str                # surfaced by /idle_tools list
    weight: float                   # weighted-pick base
    cooldown_s: float               # per-rule min-gap
    predicate: Callable[[IdleToolContext], bool]
    framing: str                    # running-bit: soft slot instruction;
                                    # one-shot: riff framing
    needs_web_fetches: bool = True
    enabled_default: bool = True
    running_bit: bool = False
    bit_decay_s: float = 0.0
    opener_framing: str = ""        # only read when running_bit=True
    extra_tool_names: tuple[str, ...] = ()
```

### `IdleFireResult`

What the roller hands back on a hit; Brain turns it into an LLM prompt.

```python
@dataclass
class IdleFireResult:
    rule_name: str
    tool_name: str
    tool_output: str
    framing: str
    latency_ms: float
    success: bool
    running_bit: bool = False
    bit_decay_s: float = 0.0
    opener_framing: str = ""
    extra_outputs: dict[str, str] = {}  # keyed by tool_name
```

### `RunningBit`

Lives on `PersonalityEngine` (`tokenpal/brain/personality.py`). Active
bits are slotted into every observation + freeform prompt as a
*"Running bits you can organically weave in today"* block.

```python
@dataclass
class RunningBit:
    tag: str
    payload: dict[str, str]         # e.g. {"output": "oxymoron: ..."}
    framing: str                    # rendered soft instruction
    added_at: float                 # monotonic
    decay_at: float                 # monotonic
```

**Cap:** 3 concurrent bits. Adding a fourth evicts oldest by
`added_at` (LRU). Same-tag adds replace in place — refreshing a bit
doesn't cost a slot.

**Framing templating:** if a rule's `framing` contains `{output}`, the
orchestrator substitutes `fire.tool_output` before calling
`add_running_bit`. This lets one rule definition carry both the slot
instruction and the live detail (the word, the joke, etc.) without
tool-specific templating inside the personality module.

## Config surface

```toml
[idle_tools]
enabled = true                     # global kill switch
global_cooldown_s = 600            # min gap between any two rolls
max_per_hour = 4                   # rolling-hour rate cap

[idle_tools.rules]
# Per-rule toggles. Omitted = rule's enabled_default (True for all).
# morning_word = true
# todays_joke_bit = true
# morning_monologue = true
# …
```

Per-rule toggles are a flat `dict[str, bool]` so adding new rules
doesn't churn the schema. Writes go through
`tokenpal/config/idle_tools_writer.py::set_idle_rule_enabled`,
`::set_idle_tools_enabled`, and `::set_llm_initiated_enabled` (M3) — all
three are invoked from `/idle_tools` subcommands.

## Slash command

```
/idle_tools [list | on | off | enable <rule> | disable <rule> | roll <rule>
             | llm_on | llm_off | llm_status]
```

- `list` — rules + enabled state + ineligibility reason (cooldown,
  consent, predicate, disabled-in-config). Reason comes from
  `IdleToolRoller.rule_status`.
- `on` / `off` — flip global `enabled` flag in config.toml. Restart
  required.
- `enable` / `disable` — flip a single rule toggle. Restart required.
- `roll <rule>` — force-fire via `force_fire`, bypassing predicate and
  cooldown. Useful for tuning framing strings live.
- `llm_on` / `llm_off` — flip `llm_initiated_enabled` for the M3 path
  (issue #33). Restart required. Note: M3 also requires the
  `TOKENPAL_M3=1` env var during dogfood (M3.1-M3.3); env-gate drops
  in M3.4.
- `llm_status` — read-only dump of the M3 config flag, env-var state,
  and the per-tool cool-off table. No writes.

`roll` still records the fire in cooldown state so a manual roll
doesn't trigger an automatic one right after.

## Telemetry

Every fire writes one row to `memory.db` via
`MemoryStore.record_observation`:

```
sense_name  = "idle_tools"
event_type  = "idle_tool_fire"
summary     = rule.name                # "llm_initiated:<tool>" for M3 fires
data        = {
  "tool":          rule.tool_name,
  "emitted":       bool,              # False if filter_response swallowed it
  "tool_success":  bool,              # tool returned non-empty output
  "running_bit":   bool,
  "latency_ms":    int,
  "source":        str,               # "deterministic" or "llm_initiated" (M3)
  "filter_reason": str,               # present on swallows — see commentary-gate.md
}
```

The `source` field is derived in `Brain._record_idle_fire` from the
`rule_name` prefix - no schema migration when M3 landed; pre-M3 rows
just lack the field. M3 declines (model picked no tool) write nothing.

`filter_reason` is one of the `FilterReason` enum values from
`tokenpal/brain/personality.py` (`drifted`, `anchor_regurgitation`,
`cross_franchise`, `too_short`, `silent_marker`, `too_short_post_cleanup`),
plus the idle-tool-specific `near_duplicate` and `empty`. It's missing
from the `data` row on successful emits — that's the cheap signal for
"success vs. swallow" when filtering telemetry.

```sql
-- All swallows by reason in the last 24h (tune framings with this)
SELECT json_extract(data_json, '$.filter_reason') AS reason, COUNT(*)
FROM observations
WHERE sense_name = 'idle_tools'
  AND timestamp > strftime('%s', 'now', '-1 day')
  AND json_extract(data_json, '$.emitted') = 0
GROUP BY 1 ORDER BY 2 DESC;
```

These rows make the `memory_query` tool eventually able to surface
callbacks like *"you've heard 12 jokes this week"*. Today they're
also the paper trail for debugging framing drift — without
`filter_reason`, a silent roller is indistinguishable from a broken one.

## Adding a rule

1. Write the predicate. Keep it cheap — it runs every tick when the
   gate is quiet. A broken predicate is swallowed and logged at DEBUG;
   it doesn't poison the rest of the roll. Signals available in
   `IdleToolContext`: time (`now`, `hour`, `weekday`), session state
   (`session_minutes`, `first_session_of_day`), active sense readings,
   mood + weather summary, `time_since_last_comment_s`, consent flag,
   and the three personalization fields (`daily_streak_days`,
   `install_age_days`, `pattern_callbacks`).
2. If the predicate needs a NEW signal not on `IdleToolContext`, extend
   the dataclass + `build_context` helper + `Brain._build_idle_context`
   together. Default the new field to a safe empty so existing tests
   don't need boilerplate. Memory-backed signals should session-cache
   or TTL-cache; `_build_idle_context` fires on every brain tick.
3. Append an `IdleToolRule(...)` to `M1_RULES` with a unique `name`.
4. Pick a cooldown. Evergreens (`moon_phase`, `word_of_the_day`,
   `joke_of_the_day`, `on_this_day`, `sunrise_sunset`) get a warm
   cache for free; everything else hits the network on each fire.
   Local tools (`memory_query`) are always fast — set
   `needs_web_fetches=False` so the rule survives offline.
5. If you want the rule's output to ride along multiple subsequent
   prompts, set `running_bit=True` + `bit_decay_s=<seconds>`. Write
   the framing as a soft `{output}`-templated instruction.
6. If the rule announces itself, set `opener_framing` to the one-shot
   riff instructions. Empty = silent registration.
7. If the rule is a multi-tool chain, list the companion tools in
   `extra_tool_names`. They'll share the evergreen cache when
   applicable.
8. Add a toggle stub in `config.default.toml` under
   `[idle_tools.rules]` and a comment line.
9. Add unit tests in `tests/test_brain/test_idle_rules_predicates.py`
   for edge-time correctness and
   `tests/test_brain/test_idle_tools_monologue.py` for chain / running
   behavior.

## Privacy posture

- No new network calls were added for this feature — the tools it
  invokes were already registered actions used elsewhere.
- Network rules silently drop when `web_fetches` consent is missing;
  there's no nag UI. `memory_recall` keeps the feature alive without
  consent.
- Sensitive-app checks from the observation path still apply: if
  `contains_sensitive_term(snapshot)` is True, the roller never fires.
- `memory_query` only reads the local `memory.db`; no remote recall.
- Tool outputs land inside the LLM prompt, which stays local. External
  text is already filtered through the response cleanup path
  (`_clean_llm_text`, `_has_cross_franchise`, `is_clean_english`).

## LLM-initiated rolls (M3)

The deterministic M1+M2 path covers time-of-day rituals well, but leaves
gaps in the middle of normal sessions where no predicate matches and the
LLM might still know a flavor tool would land. M3 (issue #33) lets the
model decide. Lives in `tokenpal/brain/idle_tools_m3.py`.

**Wiring.** `_maybe_fire_llm_initiated_tool` runs BEFORE
`_maybe_fire_idle_tool` inside the idle-eligible block in the brain
loop. On a hit it consumes the tick and the deterministic roller is
skipped; on a decline (or any gate failure) the deterministic path
runs unchanged. Both rollers share one `FireTracker` instance so cross-
path cooldowns work.

**Hard gates** (in order, cheapest first):

1. `idle_tools.llm_initiated_enabled` config flag (default off).
2. `TOKENPAL_M3=1` environment variable. Required during dogfood
   (stages M3.1-M3.3); dropped in M3.4.
3. `personality.check_sensitive_app(snapshot)` - same gate as the
   deterministic path.
4. `personality.mood_role in {"sleepy", "concerned"}` - skip when the
   user is winding down or stressed. The original plan called for a
   "focused" mood block too, but no `Mood.FOCUSED` enum exists; if
   telemetry shows we want one, add it then.

**Soft gates** inside `LLMInitiatedRoller.maybe_fire`:

5. M3-specific cooldown (`llm_initiated_cooldown_s`, default 30 min)
   between any two M3 fires. Separate from the deterministic 180s.
6. M3-specific rolling-hour cap (`llm_initiated_max_per_hour`, default
   1). Separate counter from the deterministic max_per_hour=6.
7. Shared global rolling-hour cap with the deterministic path - so
   noise stays bounded across both paths combined.
8. Per-tool cool-off via `FireTracker.last_by_tool`. A deterministic
   fire of `moon_phase` writes `last_by_tool["moon_phase"]=now` and
   blocks M3 `moon_phase` for the rule's cooldown (24h). The reverse
   does NOT hold: M3 fires don't block deterministic. Asymmetric by
   design - M3 is the conservative experimental path.
9. Circuit breaker: same M3 tool picked `CONSECUTIVE_PICK_LIMIT`=3
   times in a row triggers a `CIRCUIT_COOLOFF_S`=2h block on that tool
   for both the M3 and deterministic paths.

**Catalog.** Curated 9-tool subset; the LLM never sees the full action
registry. Defined in `idle_tools_m3.py:M3_CATALOG`. Per-tool cool-offs
mirror the tightest deterministic rule cooldown for the same tool
(`PER_TOOL_COOLOFF_S`).

**Single tool turn.** M3 calls `generate_with_tools` once. If the
response includes a `tool_calls[0]` in the catalog, the action is
invoked, then `_generate_tool_riff` (the existing deterministic riff
path) runs LLM turn 2 to compose the in-character line - the personality
prompt + filter pipeline are reused unchanged. M3 does NOT round-trip
the tool call back to the model on turn 2; the personality prompt frames
the output instead. This trades "model sees its own tool call" for
"voice stays in character" and avoids Ollama tool-format drift.

**memory_query metric default.** The action requires a `metric` enum
arg. If the LLM omits it, `_sanitize_args` injects
`MEMORY_QUERY_DEFAULT_METRIC = "session_count_today"` (the lowest-
privacy probe; matches the deterministic floor in `idle_tools._MEMORY_RECALL_METRICS`).

**Telemetry.** Successful M3 fires write `idle_tool_fire` rows with
`data["source"] = "llm_initiated"` and `summary = "llm_initiated:<tool>"`.
Deterministic rows carry `data["source"] = "deterministic"`. The
distinction is derived in `Brain._record_idle_fire` from the `rule_name`
prefix - no schema migration needed. Declines (LLM picked no tool) do
NOT write telemetry rows.

**Slash commands.**

- `/idle_tools llm_on` / `llm_off` - flip
  `idle_tools.llm_initiated_enabled` in `config.toml`.
- `/idle_tools llm_status` - dump current config + env-var state.

**Verification (manual).**

```sh
# 1. Enable both gates.
export TOKENPAL_M3=1
./run.sh
# In TokenPal: /idle_tools llm_on

# 2. Wait for a freeform tick (>10 min idle). Watch chat log for
#    "TokenPal (idle-tool llm_initiated:<tool> -> <tool>): ..."

# 3. Confirm telemetry.
sqlite3 ~/.tokenpal/memory.db \
  "SELECT summary, data FROM observations \
   WHERE event_type='idle_tool_fire' \
   AND summary LIKE 'llm_initiated:%' \
   ORDER BY ts DESC LIMIT 3;"
```

## Known non-goals

- **Always-on M3.** `llm_initiated_enabled` defaults to false even
  after M3.4 (the env-var drop); user must opt-in via `/idle_tools
  llm_on`.
- **Per-voice rule muting.** Every voice gets every rule. If a voice
  produces cringe with a specific tool, tune the rule's framing string
  or cooldown instead.
- **Author-birthday `callback_book` rule.** Cut during M2 - reliable
  detection would need an LLM classifier we don't have yet.
