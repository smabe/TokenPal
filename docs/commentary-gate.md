# Commentary Gate

The brain loop's commentary gate decides, every tick, whether the buddy
should speak. Three emission paths sit downstream of it: **observation**
(gated comment on a sense change), **freeform** (unprompted in-character
thought), and **idle-tool roll** (contextual tool-flavored riff during
quiet stretches; see `idle-tool-rolls.md`). A fourth category —
high-signal events (git-nudge, rage-check, drift-nudge) — bypasses the
gate entirely.

Read this before editing the gate, the near-duplicate guard,
`_handle_suppressed_output`, `filter_response`, or any of the
`_generate_*` emission paths in `tokenpal/brain/orchestrator.py` and
`tokenpal/brain/personality.py`.

## Why it exists

A naive loop either talks constantly ("you're still in the same app"
every minute) or falls silent after the first few observations. The
gate balances these by scoring the current context's interestingness
against a dynamic threshold and applying a stack of pacing, silence,
and recency guards on top.

## Per-tick flow

```
┌─ brain loop tick ────────────────────────────────────────────┐
│                                                              │
│  poll senses → ContextWindowBuilder.ingest                   │
│                                                              │
│  high-signal bypass:                                         │
│    rage? → _generate_rage_check                              │
│    git nudge? → _generate_git_nudge                          │
│    drift? → _generate_drift_nudge                            │
│                                                              │
│  else, _should_comment():                                    │
│    - not paused / in_conversation / long_task                │
│    - elapsed > dynamic cooldown (30-180s scaled to activity) │
│    - not in _forced_silence_until window                     │
│    - consecutive < _FORCED_SILENCE_AFTER                     │
│    - < _MAX_COMMENTS_PER_WINDOW in last 5min                 │
│    - interestingness >= threshold (with boredom bonus)       │
│                                                              │
│  true:  _generate_comment                                    │
│  false: _should_freeform? → _generate_freeform_comment       │
│  false: _idle_tools_eligible? → _maybe_fire_idle_tool        │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

Each `_generate_*` does the LLM call, runs the response through
`filter_response`, checks near-duplicate, and either emits via
`_emit_comment(..., acknowledge=True)` or routes failure through
`_handle_suppressed_output`.

## Interestingness + threshold

`ContextWindowBuilder.interestingness()` in `context.py` scores the
current snapshot against per-sense weights:

```python
_SENSE_WEIGHTS = {
    "app_awareness":  0.3,
    "idle":           1.0,   # idle transitions are always load-bearing
    "hardware":       0.3,
    "time_awareness": 0.15,
    "productivity":   0.1,
    "music":          0.2,
    "weather":        0.0,   # enriches context only
    "git":            0.8,   # commits/branch switches are high-signal
}
```

A sense reading contributes its full weight when it's new or its summary
differs from the last *acknowledged* summary. The score is capped at 1.0.

Threshold adjustments in `_should_comment`:

- **Nighttime bump** — +0.2 for 00-05, +0.1 for 22-23 (quieter buddy after hours)
- **Activity bonus** — subtracts up to 0.15 when the user is busy (encourages spontaneity)
- **Boredom bonus** — after 10+ min of silence and *some* real change (score > 0), the threshold can drop to a floor of 0.10

The floor matters: after the fixes in this session, the gate no longer
loops on the same stale delta forever (see "Acknowledge-on-suppression"
below), but a score of 0.10 is still permissive. If the observation path
feels chatty, raising the floor is the next knob.

## Pacing guards

- **Dynamic cooldown** — `max(_cooldown, ceiling - activity * 60)`.
  Ceiling is 180s during sustained idle (AFK composite), 90s otherwise.
  Plus 0-15s of jitter so the buddy doesn't feel like a metronome.
- **Comment burst cap** — `_MAX_COMMENTS_PER_WINDOW=8` in the last
  5min, counted via `_comment_timestamps`.
- **Consecutive-comments breaker** — `_FORCED_SILENCE_AFTER=3`
  sequential comments installs a 120s forced silence window, tagged via
  `_forced_silence_until`. This window blocks the comment and freeform
  paths but **not idle-tool rolls** (see below).

## Near-duplicate + prefix-lock guards

After the LLM generates, `_is_near_duplicate` catches two failure modes:

- **Trigram Jaccard** — `_trigram_set(new) & _trigram_set(prior)` ≥
  `_NEAR_DUPLICATE_JACCARD=0.70` against any of the last 10 outputs in
  `_recent_outputs`.
- **Leading-prefix lock** — the lowercase punctuation-free first 5
  tokens of the new line repeated in 2+ recent outputs. Catches
  template drift where the tail varies but the lead is identical
  ("Jake, good cop, this X got more Y than Z").

A suppressed near-duplicate does *not* emit. It *does* run through
`_handle_suppressed_output`, which is where the next two mechanisms live.

## Acknowledge-on-suppression (the 2026-04-20 fix)

`_handle_suppressed_output` is the single funnel for "we generated but
couldn't emit". It does three things:

1. **Advances `_last_comment_time`** so the next tick doesn't re-fire
   immediately on the dynamic-cooldown reading "it's been forever since
   we spoke." Without this, a single stuck phrase can burn thousands of
   LLM calls overnight.
2. **Increments `_suppressed_streak`**. At
   `_FORCED_SILENCE_AFTER_SUPPRESSIONS=5` it installs a 120s forced
   silence window, same mechanism as the consecutive-comments breaker.
3. **Calls `_context.acknowledge()`**. The key semantic: we OBSERVED
   the state; we just had nothing new to say. Future ticks should score
   on fresh change, not the stale delta we already failed to comment on.

Before (3) was added, the gate kept picking "comment" on every tick
because `_prev_summaries` stayed unchanged after a suppression. A
single 2026-04-20 overnight session burned through 182 near-duplicate
riffs with zero idle-tool rolls because suppressions re-triggered each
tick. (The forced-silence window eventually caught it, but only to
produce another spam burst a minute later.)

## filter_response + FilterReason

`PersonalityEngine.filter_response` is the last line of defense between
LLM drift and the user. It runs seven checks in order; each failure
drops the response and stamps `self.last_filter_reason` with a
`FilterReason` enum value:

| Reason | Trigger |
|---|---|
| `OK` | response passed (last_filter_reason starts here every call) |
| `SILENT_MARKER` | contains `[SILENT]` |
| `TOO_SHORT` | < 15 chars raw |
| `DRIFTED` | fails `is_clean_english` (gibberish, leaked tags, non-English) |
| `ANCHOR_REGURGITATION` | verbatim voice-anchor copy (see below) |
| `CROSS_FRANCHISE` | names a character from the wrong show |
| `TOO_SHORT_POST_CLEANUP` | < 15 chars after `_clean_llm_text` strips markup |

`FilterReason` inherits from `str`, so `.value` slots into telemetry
JSON without `.value` boilerplate everywhere. The idle-tool riff path
reads `personality.last_filter_reason.value` after every
`filter_response` call and writes it to the `idle_tool_fire` telemetry
row — see `idle-tool-rolls.md` for the swallow-reason query.

### Anchor regurgitation

The overnight run that surfaced this lit up 5+ copies of
"Why do I smell like pineapples?" — a verbatim entry in the Finn
voice's `anchor_lines`. Confused small models fall back to copying a
few-shot example instead of generating new content. `is_clean_english`
passes those (the anchor IS clean English), so a dedicated guard is
required.

`_is_anchor_regurgitation` normalizes the incoming text to lowercase
alphanumerics and checks it against `_anchor_pool_normalized` — a
frozenset populated at voice-load time from every anchor >= 15 chars.
Short anchors are excluded because the length gate already handles
them and they're too generic to fingerprint (e.g. "Yeah!").

Paraphrases pass. Exact copies (modulo punctuation, whitespace, casing)
drop with reason `ANCHOR_REGURGITATION`.

## Idle-tool roll eligibility

Idle rolls run when the comment gate chose silence AND
`_idle_tools_eligible` returns True. The eligibility check is
deliberately narrower than `_should_comment`:

```python
def _idle_tools_eligible(self) -> bool:
    if not self._idle_tools_config.enabled:   return False
    if self._paused:                          return False
    if self._in_conversation:                 return False
    if self._any_long_task():                 return False
    return True
```

Crucially, the observation-path `_forced_silence_until` window does NOT
gate idle rolls. That window exists to stop near-dup LLM spam on the
observation path; idle rolls inject fresh tool output and are the right
recovery from dead air, not more silence. Before this decoupling a
single bad roll's near-dup suppression could install a 2-minute window
that then blocked the next 40+ ticks from even trying a different rule.

Idle-tool near-duplicate suppression also skips
`_handle_suppressed_output` — a rule's own cooldown is enough; we
don't want one bad framing to starve freeform + drift nudges for 2min.

## Emission invariants

- Every successful emit calls `_emit_comment(..., acknowledge=True)`
  which in turn calls `_context.acknowledge()`, updating
  `_prev_summaries`. Interestingness scores the NEXT tick against this.
- Every suppression calls `_handle_suppressed_output` which ALSO calls
  `acknowledge` (see above). Net effect: the gate never sees the same
  delta twice unless something actually changed.
- Easter eggs (3:33 AM, Friday 5 PM, Zoom, Calculator) bypass the LLM
  entirely and acknowledge the context on hit — same invariant.

If you add a new emission path, funnel its failures through
`_handle_suppressed_output` for consistency. Otherwise the LLM's stuck
loops will come back.

## Known limits

- The 0.10 boredom-floor is still permissive. If framing drift makes
  the observation path chatty again, raising this is the first knob.
- `_recent_outputs` holds 10 lines; long conversations won't detect
  duplicates against comments older than that. Not a problem today but
  a scaling ceiling.
- Idle rolls' own suppression path (near-dup on a riff) counts toward
  the rule's cooldown but doesn't affect other rules. If one chain
  rule keeps producing bad framings, only that rule goes quiet —
  the roller as a whole keeps working.
