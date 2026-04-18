# Idle Tool Rolls — Autonomous Flavor During Quiet Stretches

**Status:** M1 + M2 shipped 2026-04-17. `callback_book` cut per dogfood (hard to
curate a reliable author list without LLM classification). M3 deferred to
[issue #33](https://github.com/smabe/TokenPal/issues/33).
**Owner:** tokenpal brain loop
**Companion plan:** `plans/idle-loop-variety.md` (shipped 2026-04-17)

## Problem

The buddy's idle loop is observation-only. We've registered ~18 flavor tools
(`joke_of_the_day`, `word_of_the_day`, `on_this_day`, `trivia_question`,
`moon_phase`, `sunrise_sunset`, `random_recipe`, `weather_forecast_week`,
`book_suggestion`, `random_fact`, `crypto_price`, `currency`, `pollen_count`,
`air_quality`, `sports_score`, `timezone`, `convert`, `memory_query`) — none of
them fire unprompted. During real quiet stretches (user deep in one app, no
sense transitions, nothing for the commentary gate to latch onto) the buddy
either spams near-duplicate observations or goes completely silent.

We want a third idle-loop path: **a contextual tool-roll** that produces
in-character flavor the LLM couldn't invent from context alone — today's joke,
today's word, this day in history, tonight's moon phase — and uses those
results as prompts the buddy riffs on, sometimes across the whole day.

## Goals

- **G1** When the idle loop goes quiet, buddy produces tool-flavored comments
  that feel motivated by context (time of day, weather change, new session,
  long focus) rather than random.
- **G2** Certain tool results — notably `word_of_the_day` — anchor a
  multi-hour **running bit** so the word shows up naturally across several
  unrelated observations. Call-back comedy.
- **G3** Latency, privacy, and rate-limit posture of the existing observation
  path are preserved. Idle rolls never fire during conversation, sensitive
  apps, forced-silence windows, or when `web_fetches` consent isn't granted.
- **G4** Fully opt-in + per-rule-toggleable like `[senses]`. A user who wants
  zero network chatter can disable the whole surface in one flag.

## Non-goals

- **NG1** LLM-initiated tool-calling during idle (the model saying "let me
  look something up"). Too template-prone on small quantized models,
  non-deterministic latency. Marked M3 below, out of scope for this plan.
- **NG2** Any tool that exfiltrates local context (none of the candidate tools
  do; privacy check lives in `actions/network/_base.py` and we're not
  loosening it).
- **NG3** New tool registrations. Everything in M1+M2 uses existing
  `@register_action`s.

## Design overview

```
┌─────────── brain loop tick ──────────────────────────────────┐
│ poll senses → context window → gate decision                 │
│                                                              │
│  gate says "comment":  → _generate_comment() (unchanged)     │
│  gate says "freeform": → _generate_freeform_comment()        │
│  gate says "silence":  → NEW: IdleToolRoller.maybe_fire()    │
│                                                              │
│     maybe_fire():                                            │
│       1. check global enable + rate cap + consent            │
│       2. evaluate active IdleToolRules against context       │
│       3. weighted pick among rules that pass                 │
│       4. warm-cache lookup or invoke tool                    │
│       5. either:                                             │
│          a. regular riff → _generate_tool_riff()             │
│          b. running-bit  → PersonalityEngine.add_running_bit │
│                            (+ optional one-line announcement)│
└──────────────────────────────────────────────────────────────┘
```

Key architectural point: the roller is a **third emission path** parallel to
observation and freeform, not a replacement. It only fires when the existing
gate chose silence. This keeps commentary volume the same (still capped at
8/5min) but fills quiet stretches with higher-quality flavor instead of
forcing duplicate observations through the near-duplicate guard.

## Data model

### `IdleToolContext` — snapshot passed to predicates

```python
@dataclass(frozen=True)
class IdleToolContext:
    now: datetime                       # local time (zoneinfo)
    session_minutes: int
    first_session_of_day: bool          # memory lookup at session start
    active_readings: Mapping[str, SenseReading]
    mood: str                           # personality mood name
    weather_summary: str                # cached from weather sense, may be ""
    last_fire_by_rule: Mapping[str, float]  # monotonic
    last_fire_any: float
    running_bits_active: int
    consent_web_fetches: bool
```

### `IdleToolRule` — one trigger definition

```python
@dataclass(frozen=True)
class IdleToolRule:
    name: str                           # stable id, used in config + logs
    tool_name: str                      # matches AbstractAction.action_name
    description: str                    # human-readable, surfaced by /idle_tools list
    weight: float                       # base weight when predicate passes
    cooldown_s: float                   # per-rule min-gap between fires
    predicate: Callable[[IdleToolContext], bool]
    framing: str                        # prompt hint for the riff
    running_bit: bool = False           # if True → add_running_bit instead of riff
    bit_decay_s: float = 0.0            # only used when running_bit=True
```

### `RunningBit` — lives on `PersonalityEngine`

```python
@dataclass
class RunningBit:
    tag: str                            # "word_of_the_day", "todays_joke", etc.
    payload: dict[str, str]             # {"word": "oxymoron", "definition": "..."}
    framing: str                        # soft instruction slotted into system prompt
    added_at: float                     # monotonic
    decay_at: float                     # monotonic
```

Active (non-expired) bits are appended to every `build_prompt()` /
`build_freeform_prompt()` call as a **Today's running bits** section. The
framing is deliberately soft: *"Slip this word in when it fits naturally. Do
not force it."* The LLM decides when; the guard against over-use is the
existing near-duplicate filter from `idle-loop-variety.md`.

Cap: max 3 active bits; new-in evicts oldest on collision.

## Config surface

New section in `config.toml` (mirrors `[senses]`):

```toml
[idle_tools]
enabled = true                          # global kill switch, default ON
global_cooldown_s = 600                 # min gap between any two tool rolls
max_per_hour = 4                        # hard rate cap
suppress_during_sensitive_apps = true   # redundant with observation gate but explicit

[idle_tools.rules]
evening_moon = true
morning_word = true
monday_joke = true
weather_change = true
long_focus_fact = true
deep_lull_trivia = true
on_this_day_opener = true
lunar_override = true
morning_monologue = true                # M2 only, ignored on M1
callback_book = true                    # M2 only, ignored on M1
```

Schema additions go in `tokenpal/config/schema.py` as a new `IdleToolsConfig`
dataclass; rule toggles are a `dict[str, bool]` to stay forward-compatible as
new rules are added without schema churn.

## Lifecycle / integration points

- **Session start:** on brain construction, kick a background warmer task
  (`asyncio.create_task`) that pre-fetches daily tools: `word_of_the_day`,
  `joke_of_the_day`, `on_this_day`, `moon_phase`, `sunrise_sunset`. Cached in
  `IdleToolRoller._daily_cache` with a 6-hour TTL, so the idle-fire path
  never blocks on a cold HTTP round-trip for these evergreens.
- **Per-tick hook:** in `Brain._tick`, after `_should_comment()` returns
  False (and freeform chance also fails), call
  `self._idle_tools.maybe_fire(context)`. The roller returns
  `IdleFireResult | None`; Brain handles the two kinds.
- **Regular riff:** Brain calls `_generate_tool_riff(snapshot, tool_output,
  framing)`. New method. Prompt shape:
  ```
  [Current moment:]
  {snapshot}

  [Fresh detail to weave in, in-character:]
  {tool_output}

  [How to frame it:]
  {framing}
  ```
  Result passes through `filter_response` + `_is_near_duplicate` like any
  observation.
- **Running bit:** Brain calls
  `self._personality.add_running_bit(bit)`. Optionally, if the rule also
  declares an opener, Brain generates a one-line announcement ("Word of the
  day is *oxymoron* — fitting.") and emits it as a normal comment.
- **Rate + cooldown:** roller maintains its own `last_fire_by_rule` dict and
  a 1-hour sliding window for `max_per_hour`. Independent from the existing
  comment rate cap but additive — a fire counts toward comment rate *only*
  if it produces a visible emission (same `_emit_comment` → ts append).
- **Consent:** every network-flavored rule predicate includes
  `ctx.consent_web_fetches`. If the user hasn't granted, those rules are
  dropped from the weighted set and only offline rules (see below) remain.
  Fail-silent; no prompt shown.
- **Telemetry:** every fire (including offline) writes to the memory DB as
  `event_type = "idle_tool_fire"` with `summary = rule_name`, `data =
  {"tool": tool_name, "running_bit": bool, "success": bool, "latency_ms":
  int}`. Enables future `memory_query`-driven callbacks ("you've heard 12
  jokes this week"). See `MemoryStore.record_observation()` contract.

## M1 — contextual rolls

Ship a working idle-tool-roller with deterministic contextual picks, no
running-bit infrastructure, no chained/morning monologues.

### Files

- **NEW** `tokenpal/brain/idle_tools.py` — roller, rules registry, warm
  cache, `IdleFireResult` dataclass.
- **NEW** `tokenpal/brain/idle_rules.py` — M1 rule definitions, one per
  trigger. Data-only, importable without side effects.
- **EDIT** `tokenpal/brain/orchestrator.py` — wire roller into tick, add
  `_generate_tool_riff`.
- **EDIT** `tokenpal/config/schema.py` — `IdleToolsConfig` dataclass.
- **EDIT** `config.default.toml` — `[idle_tools]` block with sane defaults
  (`enabled=true`, all rules on, sensible cooldowns).
- **NEW** `tokenpal/config/idle_tools_writer.py` — `set_rule_enabled()` +
  `set_global_enabled()`, mirror `senses_writer.py` API.
- **EDIT** the slash-command router (wherever `/senses` lives) — add
  `/idle_tools [list|enable <rule>|disable <rule>|on|off|roll]`.
- **EDIT** CLAUDE.md — one-line section note under "Slash Commands".

### M1 rule set (9 rules — 8 network, 1 offline)

| name | tool | network? | predicate | cooldown | weight |
|---|---|---|---|---|---|
| `evening_moon` | `moon_phase` | yes | `21 <= hour < 24` | 24h | 1.0 |
| `morning_word` | `word_of_the_day` | yes | `first_session_of_day and 6 <= hour < 11` | 18h | 1.5 |
| `monday_joke` | `joke_of_the_day` | yes | `weekday == 0 and first_session_of_day` | 7d | 1.0 |
| `weather_change` | `weather_forecast_week` | yes | `"weather" in readings and reading.just_changed` | 6h | 1.2 |
| `long_focus_fact` | `random_fact` | yes | `any reading.summary contains "Deep focus"` | 2h | 0.8 |
| `deep_lull_trivia` | `trivia_question` | yes | `time_since_last_comment > 900 and mood != "focused"` | 2h | 0.6 |
| `on_this_day_opener` | `on_this_day` | yes | `first_session_of_day and 6 <= hour < 12` | 18h | 1.3 |
| `lunar_override` | `moon_phase` | yes | `is_full_moon(ctx.now) and hour >= 22` | 24h | 3.0 |
| `memory_recall` | `memory_query` | **no** | `session_minutes > 15 and time_since_last_comment > 600` | 3h | 1.0 |

`memory_recall` is the consent-free floor. `memory_query` only reads local
`memory.db`, so it's fine when `web_fetches` consent is absent. Framing:
*"You just looked something up about the user's past habits — drop one
observation about it, no numbers unless they're striking."* Query chosen
randomly from a small local set: "most-used app this week", "longest
focus streak this session", "first-app-of-day streak". Keeps the feature
alive for users who haven't opted into web fetches yet.

Framing strings for all rules are short in-character hints, tunable in
`idle_rules.py` without touching orchestration.

### Tests (M1)

- `tests/test_brain/test_idle_tools_roller.py`
  - rule selection: weighted pick is deterministic under stubbed `random`
  - cooldown: a rule that just fired is excluded
  - rate cap: `max_per_hour` honored
  - global-disabled short-circuit
  - consent-denied short-circuit
- `tests/test_brain/test_idle_rules_predicates.py`
  - each rule's predicate evaluates correctly at edge times (hour 5:59 vs
    6:00, weekday 0 vs 6, etc.)
- `tests/test_brain/test_idle_tools_writer.py`
  - round-trips: set_rule_enabled writes and re-reads
- Integration-ish: `test_orchestrator_idle_path.py`
  - gate returns silence → roller fires → `_emit_comment` called with
    filtered text containing framing's intent marker

## M2 — running bits + morning monologue + call-back chain

### Files

- **EDIT** `tokenpal/brain/personality.py` — `RunningBit` dataclass,
  `_running_bits: list[RunningBit]`, `add_running_bit()`,
  `_prune_expired_bits()`, inclusion in `build_prompt` /
  `build_freeform_prompt` system message.
- **EDIT** `tokenpal/brain/idle_rules.py` — new rules listed below; mark two
  existing rules (`morning_word`) as `running_bit=True` with `bit_decay_s =
  8 * 3600`.
- **EDIT** `tokenpal/brain/idle_tools.py` — chain-rule support: after a rule
  fires, check `follow_up_rule_name` on the rule and schedule a deferred
  single-shot fire within `follow_up_delay_s` seconds, bypassing normal
  weighted pick.

### M2 new rules + extensions

| name | extension |
|---|---|
| `morning_word` (upgrade) | `running_bit=True`, `bit_decay_s=28800` (8h). Framing: *"You learned the word '{word}' today. Slip it in naturally when it fits; once is enough. Never define it unless asked."* |
| `todays_joke_bit` (new) | `tool=joke_of_the_day`, `running_bit=True`, `bit_decay_s=14400` (4h). Framing: *"You heard this joke earlier today. Reference it with a callback if a moment comes up, tell it poorly."* |
| `morning_monologue` (new) | First session of day, 6-10am, cooldown 24h. Chains `weather_forecast_week` + `sunrise_sunset` + `on_this_day` results into one riff with framing *"You're doing your 30-second morning radio broadcast."* |
| `callback_book` (new) | Triggers 120s after `on_this_day_opener` if the result mentions a birthday of a known author; invokes `book_suggestion`. Framing: *"They were born today. Pitch the book in one line, no synopsis."* |

### New integration points (M2)

- **Running-bit slotting:** `PersonalityEngine.build_prompt` (observation)
  and `build_freeform_prompt` grow a new section after guardrails:
  ```
  [Running bits you can organically weave in today:]
  - Today's word: oxymoron — something made of contradictory parts
    (slip it in once naturally; never define unless asked)
  - Today's bad joke: "I told my wife she was drawing her eyebrows too
    high. She looked surprised." (callback only, never re-tell outright)
  ```
- **Chain scheduler:** a small `_pending_followups: list[tuple[float,
  IdleToolRule]]` on the roller; checked at the top of `maybe_fire`.
- **Eviction:** adding a 4th running bit evicts oldest by `added_at`.

### Tests (M2)

- `test_personality_running_bits.py`
  - expired bits pruned from prompt
  - max 3 active, LRU eviction
  - framing text appears verbatim in `build_prompt` output
- `test_idle_tools_chain.py`
  - fire of parent rule schedules follow-up; follow-up fires at the right
    window; follow-up bypasses weighted pick
  - follow-up still honors global cooldown + rate cap
- `test_idle_tools_monologue.py`
  - morning monologue chains three tool calls, all three outputs appear in
    the riff prompt
  - fails gracefully if any one tool errors (continues with the rest)

## Resolved decisions (2026-04-17)

1. **Offline rule in M1 — yes.** `memory_recall` uses `memory_query`
   against the local DB so the feature has a pulse without `web_fetches`
   consent. Listed in the M1 rule table.
2. **Per-voice disable list — cut.** No per-voice rule muting. Revisit only
   if dogfood produces cringe.
3. **Telemetry — yes, in M1.** Every fire writes an `idle_tool_fire`
   observation. See Lifecycle → Telemetry above.
4. **M3 (LLM-initiated tool-calling) — filed as [#33](https://github.com/smabe/TokenPal/issues/33).**
   Not blocking this plan. Revisit when M2 has baked and we can evaluate
   whether Qwen3 picks tools tastefully or falls into template traps.

Follow-up thread (not part of this plan): **consent UX is too cumbersome**
per user feedback. Worth a separate plan — streamline `/consent` flow so
idle tools can come to life out-of-the-box for new users.

## Test plan (manual, after M1 ship)

- Run with `[idle_tools] enabled = true` + `morning_word` + `monday_joke`
  on a Monday morning; confirm within first 5min buddy drops a word-of-day
  or joke riff.
- Force a long focus streak (stay in one app > 30min); confirm
  `long_focus_fact` eventually rolls and produces a `random_fact`-flavored
  comment.
- Manually run `/idle_tools roll` repeatedly; confirm cooldown prevents
  back-to-back same-rule fires.
- Revoke `web_fetches` consent mid-session; confirm subsequent rolls
  fail-silent with a debug log and no error bubble.
- Enable `morning_word` + verify in chat log across an 8-hour window that
  the word appears 1-4 times across unrelated observations, never
  re-defined after the opener.

## Sequencing

- **Week of 2026-04-21:** M1 scoping + implementation + tests. One commit
  per sub-piece (schema → rules → roller → integration → slash command →
  tests) to keep the review surface small.
- **Week of 2026-04-28:** M1 dogfood + polish pass (`/simplify` after the
  schema commit, after the roller commit).
- **Week of 2026-05-05:** M2 running-bits. This is where the real magic
  lives; expect 2-3 rounds of framing-string tuning after dogfood.
- **Ship announcement in chat log itself** ("new flavor: try /idle_tools
  on") as the first running-bit callback eaten by the buddy.
