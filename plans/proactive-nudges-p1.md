# proactive-nudges-p1 — schedule model + `reminders` table

You are phase `p1` of the `proactive-nudges` plan. This phase delivers, as one commit, a serialisable `Schedule` value type and the `memory.db` table that stores armed reminders. **No behaviour changes.** The scheduler still runs on closures and the four old tools still work; nothing imports what you build until p2.

## Locked decisions
See the master `plans/proactive-nudges.md`. Binding here:
- **Wall clock, not monotonic.** `time.monotonic()` on macOS is `mach_absolute_time()` and excludes system sleep (measured: 2 h 56 m behind over 13 days uptime), and its epoch is per-boot, so a stored monotonic reading is meaningless in a later process. Every persisted timestamp is `time.time()`, matching every other stored timestamp in `memory.db`.
- **Two schedule kinds, one type:** a fixed interval, and a daily time of day. No weekdays, windows or quiet hours (master Non-goals).
- **A missed occurrence fires once, never replayed** (operator decision 8). The repo's precedent is `has_shown_eod` (`memory.py:589,602`), which is date-keyed so it fires at most once per calendar day however many times the app relaunches.
- **Reminders get their own table.** `_prune()` (`memory.py:804-820`) deletes from `observations` older than `retention_days`, so a reminder stored there would vanish after 30 days.

## Work
- Scope trace: PREREQUISITE — p2 cannot own a schedule it cannot represent, and p3 cannot list what is armed unless it is stored.
- `tokenpal/brain/schedule.py` — new module. Shape (proposal; names are proposals, shapes are contract):
  ```python
  @dataclass(frozen=True)
  class Schedule:
      kind: Literal["interval", "daily"]
      interval_s: float | None = None      # kind="interval"
      at_hour: int | None = None           # kind="daily", local wall clock
      at_minute: int | None = None

      def next_due_at(self, after: float) -> float
      # Wall-clock epoch seconds of the next fire strictly after `after`.
      # interval: after + interval_s.
      # daily: today's local at_hour:at_minute if strictly later than `after`,
      # else tomorrow's -- recomputed from local calendar each time, never by
      # adding 86400, so a DST transition does not drift it by an hour.

      def to_row(self) -> dict[str, Any]
      # The FOUR schedule columns only -- kind, interval_s, at_hour, at_minute.
      # id/label/armed_at/next_due_at/last_fired_at belong to the reminder row,
      # not to the schedule; the caller assembles those.

      @classmethod
      def from_row(cls, row: Mapping[str, Any]) -> Schedule
      # Reads those four keys and ignores any others, so a full reminder row
      # can be passed straight in.

      @classmethod
      def interval_from_minutes(cls, minutes: object) -> Schedule
      @classmethod
      def daily_from_hhmm(cls, raw: object) -> Schedule
      # The two constructors the tool calls. They own PARSING as well as
      # validation -- "25:70", "quarter past" and a non-integer minute count all
      # raise ValueError here, not in the tool -- and their messages name the
      # TOOL's argument ("every_min", "at"), because p3 propagates them verbatim.
  ```
  Validation *and parsing* live here and are the single source: interval bounds `_MIN_INTERVAL_S`/`_MAX_INTERVAL_S` (60 s to 24 h, matching today's 1-1440 minutes at `reminders.py:78-84`), `0 <= at_hour <= 23`, `0 <= at_minute <= 59`, and exactly the fields its `kind` requires. Raise `ValueError` naming the offending **tool argument**; p3 turns that into a refusal string verbatim, so a message naming `at_hour` would surface a field the tool schema does not expose.
- `tokenpal/brain/memory.py` — append `_migration_5_reminders` to `_MIGRATIONS` (`:177-182`). `CURRENT_SCHEMA_VERSION = len(_MIGRATIONS)` (`:184`) picks it up with no constant to bump. Table `reminders`, all statements `CREATE TABLE IF NOT EXISTS` (the migration must survive a re-run — DDL and the version bump are autocommit, so a mid-body failure leaves partial tables durable with the version un-bumped). Columns: `id TEXT PRIMARY KEY` (user-facing reminder id), `label TEXT NOT NULL`, `kind TEXT NOT NULL`, `interval_s REAL`, `at_hour INTEGER`, `at_minute INTEGER`, `armed_at REAL NOT NULL`, `next_due_at REAL NOT NULL`, `last_fired_at REAL`. Accessors beside the `active_intent` ones (`:553-587`), same `INSERT OR REPLACE` idiom, same `with self._lock:` discipline, same silent no-op when `not self._enabled or not self._conn`: `upsert_reminder`, `delete_reminder`, `list_reminders`, `mark_reminder_fired`.
  **`list_reminders() -> list[dict[str, Any]]`, keyed by column name.** The store sets no `row_factory` (`memory.py:268-272`; `grep -rn row_factory tokenpal/` is empty), so every existing accessor returns bare tuples — `get_recent_summaries -> list[tuple[float, str]]` (`:527-545`). A nine-element tuple here would be undestructurable by p2's hydrate and p3's `list`, and `Schedule.from_row(Mapping)` could not consume it. Build the dicts inside the accessor.
- `tests/test_brain/test_schedule.py` — new:
  1. interval: `next_due_at(t)` is `t + interval_s`; bounds rejected at both ends with the field named.
  2. daily, target later today → today's instant; target already passed → tomorrow's; target exactly equal to `after` → tomorrow's (strictly after).
  3. **DST spring-forward**: a `02:30` daily schedule on the transition date. Assert the concrete answer the implementation gives and state it in a comment — `02:30` does not exist that day, and naive `.replace()` resolves it to `03:30`. Pin whichever behaviour ships so it cannot change silently.
  4. **DST fall-back**: a `01:30` daily schedule on the transition date, where the local time exists twice and `fold=0`/`fold=1` differ by exactly 3600 s. Pin which one fires.
  5. **Daily does not drift across DST**: the interval between consecutive `next_due_at` results spanning a transition is 23 h or 25 h, not 24 h. This is the case that proves the recompute-from-calendar rule rather than `+86400`.
  6. round-trip: `Schedule.from_row(s.to_row()) == s` for both kinds; a row with a `kind` its fields do not match raises.
  7. `daily_from_hhmm("25:70")`, `("quarter past")`, `("")` and `interval_from_minutes("soon")` each raise `ValueError` whose message names `at` or `every_min` — the argument the *tool* exposes.
  Pin the zone in a fixture: `monkeypatch.setenv("TZ", "America/New_York")` then `time.tzset()`, with `pytest.mark.skipif(not hasattr(time, "tzset"), ...)` for Windows. **There is no existing idiom to follow** — `grep -rn "tzset" tokenpal/ tests/` returns nothing and the repo's only `zoneinfo` user is `tokenpal/actions/utilities/timezone.py`, so this is the first of its kind. Do not spend time searching for a precedent.
- `tests/test_brain/test_memory_migrations.py` — add: a synthetic v4 file upgrades to `CURRENT_SCHEMA_VERSION` with its pre-existing rows intact and a `reminders` table present (follow `test_chat_log.py:20` for the table-existence idiom); a reminders round-trip through `upsert_reminder`/`list_reminders`/`delete_reminder`; `upsert_reminder` twice on one id leaves one row.

## Decisions & findings
### Decision: `Schedule` lives in `tokenpal/brain/`, not `tokenpal/util/`  *(status: active)*
- **Rationale:** it is domain state the brain owns, and p2's scheduler is its only consumer. `tokenpal/util/` is leaf-level; `util/paths.py` importing `brain.personality` is already the repo's one inversion and does not need a second.
- **Evidence:** `tokenpal/util/__init__.py` is empty (0 bytes); the existing `util → brain` imports are `util/paths.py:11` and `util/untrusted_text.py:12`, both reaching `tokenpal.brain.personality`. A third is not wanted.

### Decision: validation lives in `Schedule`, not the tool  *(status: active)*
- **Rationale:** today it is split three ways and the layers disagree — `ProactiveScheduler.register` raises on `interval_s <= 0` (`proactive.py:77-78`, unreachable from production), the actions pre-validate 1-1440 minutes (`reminders.py:78-84`), and `bedtime_wind_down`'s `interval_min` is accepted at `reminders.py:205` but absent from its advertised schema (`:163-172`) so the model cannot discover it.
- **Evidence:** cited inline.

## Failure modes to anticipate
- **`CREATE TABLE IF NOT EXISTS` will not add a column later.** Probed: re-running it against an existing table with a new column is a silent no-op, and the first insert naming that column raises `OperationalError` at runtime, not at startup. Get the column set right now; a later addition needs its own `ALTER TABLE` migration, which has no `IF NOT EXISTS` form and so cannot be made re-run-safe the way the existing ones are.
- **Never reorder or delete a `_MIGRATIONS` entry.** `CURRENT_SCHEMA_VERSION` is `len(_MIGRATIONS)`, so the list index *is* the stored version number. Append only.
- **Naive `datetime` is the house style** — `personality.py:815,836,911` and the brain generally use bare `datetime.now()`; `idle_rules.py` compares an hour already on the context object. Do not introduce `zoneinfo` here; the DST cases are about pinning what naive local arithmetic actually does, not about fixing it.
- `time.time()` is subject to NTP steps and manual clock changes in a way `time.monotonic()` is not. That is the unavoidable cost of decision 8; do not try to hedge it with a second clock in the stored representation.
- `next_due_at()` is **strictly after** its argument while p2's `tick` fires on `now >= next_due_at`. That pairing is deliberate: a daily reminder fired at exactly its instant re-arms for tomorrow rather than re-firing. Do not "fix" either half alone.
- Storing `next_due_at` as a column duplicates what `Schedule.next_due_at()` computes. That is deliberate — it is what makes "what is due?" a cheap query on the brain's 2-second hot path, where `MemoryStore` is blocking and shares its lock with the Qt thread. Keep them consistent on every write.

## Done criteria
- `tests/test_brain/test_schedule.py` passes, including both DST cases with their answers asserted rather than described, and the no-drift case.
- A v4 `memory.db` opens at `CURRENT_SCHEMA_VERSION` with `reminders` present and its prior rows intact; running `setup()` twice is idempotent.
- A `Schedule` survives `to_row` → sqlite → `list_reminders` → `from_row` unchanged, verified against a real file on disk rather than a dict.
- `_prune()` leaves `reminders` untouched: insert a row with `armed_at` older than `retention_days`, run `setup()`, and it is still there. This is the stated reason for a separate table (`memory.py:804-820`) and nothing else asserts it.
- `grep -rn "brain.schedule\|upsert_reminder\|list_reminders\|delete_reminder\|mark_reminder_fired" tokenpal/` returns **only** `tokenpal/brain/memory.py` — this phase adds capability, not behaviour. (A bare `Schedule|reminders` grep is useless: it already has 14 hits from `ScheduledNudge`, `ProactiveScheduler`, `focus/reminders.py` and `catalog.py:175`.)
- `pytest` green; `ruff check tokenpal/` and `mypy tokenpal/ --ignore-missing-imports` clean.
