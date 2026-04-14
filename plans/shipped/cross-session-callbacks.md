# Cross-Session Memory Callbacks

## Goal
Add pattern detection to MemoryStore so the buddy makes behavioral callbacks ("you open Twitter every Monday morning before your IDE") instead of just raw visit counts. The buddy should feel like a friend who remembers your habits.

## Non-goals
- Screen capture / content-aware memory (privacy boundary)
- Cross-session conversation recall ("remember when you asked me about X")
- Changing the observation recording schema — current `observations` table is fine
- LLM-powered pattern summarization at query time (too slow, too many tokens)
- Changing retention policy (30 days is fine)

## Files to touch
- `tokenpal/brain/memory.py` — add `aggregate_daily_summary()`, `get_pattern_callbacks()`, and supporting query methods
- `tokenpal/brain/orchestrator.py` — call `get_pattern_callbacks()` alongside existing `get_history_lines()` at line ~502
- `tokenpal/brain/personality.py` — format callback lines into prompt (new `{callbacks_block}` in templates, ~line 580-605)

## Approach

### Phase 1: Daily aggregation
- `aggregate_daily_summary(date: str)` — query observations for a given date, compute: top apps + visit counts, total active minutes (session_start→session_end), total idle minutes (idle_return events), and write to `daily_summaries` table
- Call on startup for yesterday (if not already summarized) — cheap, one query
- Backfill: on first run, aggregate all historical dates that are missing summaries

### Phase 2: Pattern detection
Pure SQL + Python over `daily_summaries` + `observations`. No LLM calls. Methods:

1. **`_detect_day_of_week_patterns()`** — "You open X on Mondays more than any other day"
   - Query: app visits grouped by weekday, look for ≥2x skew toward a single day (min 3 data points)
   
2. **`_detect_time_of_day_patterns()`** — "You always start with Twitter before 9 AM"
   - Query: first-app-per-session bucketed by hour, look for consistent first-app across ≥3 sessions
   
3. **`_detect_streaks()`** — "You've used VS Code every day for 12 days straight"
   - Query: consecutive days with app_switch for a given app in daily_summaries

4. **`_detect_rituals()`** — "You always go Twitter → Slack → VS Code in the first 10 minutes"
   - Query: first 3 app switches per session, look for recurring sequences across ≥3 sessions

### Phase 3: Callback formatting
- `get_pattern_callbacks(max_callbacks: int = 3) -> list[str]` — public API, calls detection methods, returns natural-language one-liners
- Each callback is a factual observation, NOT the joke — the LLM adds the humor
- Examples: "You've opened Twitter first thing in 4 of your last 5 Monday sessions", "VS Code streak: 12 consecutive days", "Your usual morning sequence is Slack → GitHub → VS Code"
- Deduplicate: don't repeat the same callback within 3 sessions (track in a `callback_history` table or in-memory set keyed by session_id)

### Phase 4: Wire into prompts
- Orchestrator passes callbacks alongside memory_lines to personality
- Personality formats as: "Patterns you've noticed:\n- callback1\n- callback2"
- Separate from the existing "What you remember from before" block — callbacks are behavioral insights, history lines are raw facts
- Budget: ~150 tokens max for callbacks (3 lines × ~50 tokens)

## Failure modes to anticipate
- **Sparse data** — user with <5 sessions won't have meaningful patterns. Gate each detector with minimum data thresholds
- **Stale patterns** — "you always use X" but they stopped 2 weeks ago. Weight recent data higher, require ≥1 occurrence in last 7 days
- **Too many callbacks** — flooding the prompt. Hard cap at 3 per comment cycle, rotate which patterns surface
- **Sensitive apps** — pattern detection must respect the same app exclusion list as app_awareness (banking, passwords, health, messaging)
- **Performance** — daily_summaries queries are cheap but pattern detection runs multiple queries. Cache results for the session (patterns don't change mid-session)
- **Thread safety** — all queries go through existing `self._lock`, same as current methods
- **Backfill on first run** — could be slow with 173 days of data. Batch in chunks of 30 days, log progress

## Done criteria
- `aggregate_daily_summary()` populates `daily_summaries` for all historical dates on first run, then yesterday on subsequent runs
- At least 3 pattern detectors (day-of-week, time-of-day, streaks) return meaningful callbacks on real data
- Callbacks appear in LLM prompts as a separate block from raw history
- Sensitive apps are excluded from pattern detection
- Pattern detection is cached per session (no repeated queries)
- `pytest` passes, `ruff check` clean, `mypy` clean

## Notes
- Cross-platform: all pure Python + SQLite, no platform-specific code
- Callbacks only in observation prompts (not freeform/conversation), matching existing memory_block behavior
- Sensitive app list in personality.py:182-189 must be passed to MemoryStore for pattern filtering

## Parking lot
