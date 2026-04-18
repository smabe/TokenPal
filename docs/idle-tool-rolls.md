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
cannot inflate the comment rate. All other posture — pacing, 8-per-5min
cap, near-duplicate guard, sensitive-app silence, forced-silence
windows — is preserved.

## Rule catalog

All 11 rules live in `tokenpal/brain/idle_rules.py::M1_RULES`. Each is a
frozen `IdleToolRule` dataclass.

| Rule | Tool | Window / predicate | Cooldown | Running-bit? |
|---|---|---|---|---|
| `evening_moon` | `moon_phase` | 21:00–23:59 | 24h | — |
| `morning_word` | `word_of_the_day` | first-session 6–10, morning | 18h | 8h |
| `monday_joke` | `joke_of_the_day` | Mon first-session 6–10 | 7d | — |
| `weather_change` | `weather_forecast_week` | weather reading just changed | 6h | — |
| `long_focus_fact` | `random_fact` | any reading contains "Deep focus" | 2h | — |
| `deep_lull_trivia` | `trivia_question` | >15min since last comment, not focused | 2h | — |
| `on_this_day_opener` | `on_this_day` | first-session 6–12 | 18h | — |
| `lunar_override` | `moon_phase` | full-moon approx + hour ≥ 22 | 24h | — |
| `todays_joke_bit` | `joke_of_the_day` | 11–14 midday lull, settled | 12h | 4h (silent) |
| `morning_monologue` | chain of 3 | first-session 6–9 | 24h | — |
| `memory_recall` | `memory_query` | >15min session + >10min silence | 3h | — |

**Offline floor:** `memory_recall` is the only rule with
`needs_web_fetches=False`. Every other rule silently drops when the
user hasn't granted the `web_fetches` consent category; `memory_recall`
keeps the feature alive without network access.

**Running bits:** `morning_word` registers today's word as a multi-hour
callback (`bit_decay_s=28800`), then emits an opener line. For the next
8 hours the word rides along every prompt as a soft *"slip it in once
naturally, never re-define"* instruction. `todays_joke_bit` does the
same for a joke but silent — no opener, callback-only, 4h decay.

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
    @property
    def hour(self) -> int
    @property
    def weekday(self) -> int  # Mon == 0
```

`first_session_of_day` uses a memory-store lookup in
`Brain._compute_first_session_of_day()`. It's True only until the first
emission of the day, not across the whole first session.

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
`tokenpal/config/idle_tools_writer.py::set_idle_rule_enabled` and
`::set_idle_tools_enabled` — the writer is invoked from
`/idle_tools enable <rule>` and friends.

## Slash command

```
/idle_tools [list | on | off | enable <rule> | disable <rule> | roll <rule>]
```

- `list` — rules + enabled state + ineligibility reason (cooldown,
  consent, predicate, disabled-in-config). Reason comes from
  `IdleToolRoller.rule_status`.
- `on` / `off` — flip global `enabled` flag in config.toml. Restart
  required.
- `enable` / `disable` — flip a single rule toggle. Restart required.
- `roll <rule>` — force-fire via `force_fire`, bypassing predicate and
  cooldown. Useful for tuning framing strings live.

`roll` still records the fire in cooldown state so a manual roll
doesn't trigger an automatic one right after.

## Telemetry

Every fire writes one row to `memory.db` via
`MemoryStore.record_observation`:

```
sense_name  = "idle_tools"
event_type  = "idle_tool_fire"
summary     = rule.name
data        = {
  "tool":         rule.tool_name,
  "emitted":      bool,              # False if filter_response swallowed it
  "tool_success": bool,              # tool returned non-empty output
  "running_bit":  bool,
  "latency_ms":   int,
}
```

These rows make the `memory_query` tool eventually able to surface
callbacks like *"you've heard 12 jokes this week"*. Today they're
just a paper trail for debugging framing drift.

## Adding a rule

1. Write the predicate. Keep it cheap — it runs every tick when the
   gate is quiet. A broken predicate is swallowed and logged at DEBUG;
   it doesn't poison the rest of the roll.
2. Append an `IdleToolRule(...)` to `M1_RULES` with a unique `name`.
3. Pick a cooldown. Evergreens (`moon_phase`, `word_of_the_day`,
   `joke_of_the_day`, `on_this_day`, `sunrise_sunset`) get a warm
   cache for free; everything else hits the network on each fire.
4. If you want the rule's output to ride along multiple subsequent
   prompts, set `running_bit=True` + `bit_decay_s=<seconds>`. Write
   the framing as a soft `{output}`-templated instruction.
5. If the rule announces itself, set `opener_framing` to the one-shot
   riff instructions. Empty = silent registration.
6. If the rule is a multi-tool chain, list the companion tools in
   `extra_tool_names`. They'll share the evergreen cache when
   applicable.
7. Add a toggle stub in `config.default.toml` under
   `[idle_tools.rules]` and a comment line.
8. Add unit tests in `tests/test_brain/test_idle_rules_predicates.py`
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

## Known non-goals

- **LLM-initiated tool-calling** during idle (the model saying "let me
  look something up"). Templated traps + non-deterministic latency on
  small quantized models make this unsafe to ship without more work.
  Tracked at [issue #33](https://github.com/smabe/TokenPal/issues/33).
- **Per-voice rule muting.** Every voice gets every rule. If a voice
  produces cringe with a specific tool, tune the rule's framing string
  or cooldown instead.
- **Author-birthday `callback_book` rule.** Cut during M2 — reliable
  detection would need an LLM classifier we don't have yet. Revisit
  alongside #33.
