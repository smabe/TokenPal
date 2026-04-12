# Plan: SQLite Session Memory [SHIPPED]

## Context

TokenPal has in-memory running gags (app visit counts, session duration) that reset on restart. Session memory persists this to SQLite so the buddy can make cross-session callbacks like "Chrome visit #47 total" or "You always start on Twitter on Mondays." The brainstorm called this THE killer feature.

**Security approach:** File permissions (0o600) + `~/.tokenpal/` data dir. Full encryption (SQLCipher) is overkill for app names and timestamps. Architecture is encryption-ready — all DB access goes through a `_connect()` factory method that can be swapped for SQLCipher later.

## Changes

### 1. New file: `tokenpal/brain/memory.py`

**`MemoryStore` class:**
- Constructor: `db_path: Path`, `retention_days: int`, `enabled: bool`
- Generates `session_id` (UUID4 hex, 8 chars) on construction
- `_connect()` factory → `sqlite3.connect(check_same_thread=False)` + `PRAGMA journal_mode=WAL`
- `threading.Lock` wrapping all operations
- `setup()` — mkdir, create file with 0o600 permissions, `CREATE TABLE IF NOT EXISTS`, prune on startup
- `teardown()` — record session_end, close connection

**Schema:**
```sql
CREATE TABLE observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    sense_name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    data_json TEXT,
    session_id TEXT NOT NULL
);
CREATE TABLE daily_summaries (
    date TEXT PRIMARY KEY,
    summary TEXT NOT NULL,
    top_apps TEXT,
    total_active_minutes INTEGER,
    total_idle_minutes INTEGER
);
CREATE INDEX idx_obs_time ON observations(timestamp);
CREATE INDEX idx_obs_session ON observations(session_id);
```

**Recording:** `record_observation(sense_name, event_type, summary, data=None)` — inserts with `time.time()` wall clock.

**Querying:** `get_history_lines(max_lines=10) -> list[str]` — aggregates across sessions:
- Total app visit counts (top 5 by frequency)
- Session count and total hours
- Last session summary (when, how long, main app)
- Returns formatted one-liners for prompt injection

**Pruning:** `DELETE FROM observations WHERE timestamp < now - retention_days * 86400`

### 2. Config: `tokenpal/config/schema.py` + `loader.py` + `config.default.toml`

New `MemoryConfig` dataclass:
```python
@dataclass
class MemoryConfig:
    enabled: bool = True
    retention_days: int = 30
```

Add to `TokenPalConfig`, `_SECTION_MAP`, and config.default.toml as `[memory]` section.

### 3. App wiring: `tokenpal/app.py`

- If `config.memory.enabled`, create `MemoryStore(~/.tokenpal/memory.db, config.memory.retention_days)`
- Call `setup()`, `record_session_start()` on boot
- Pass to `Brain` constructor
- Call `teardown()` on shutdown (records session_end)

### 4. Event recording: `tokenpal/brain/orchestrator.py`

- Accept `memory: MemoryStore | None = None` in Brain constructor
- New `_record_memory_events()` called each loop cycle:
  - **App switch**: compare against `_last_recorded_app`, record on change
  - **Idle return**: check readings for idle sense with "returned" data
- In `_generate_comment()`: record comment milestones (every 10th)

### 5. Prompt injection: `tokenpal/brain/personality.py`

- `build_prompt()` gets new param `memory_lines: list[str] | None = None`
- New `{memory_block}` in `_PERSONA_TEMPLATE` between session_notes and context
- Renders as: `"What you remember from before:\n- Chrome: 47 visits across 12 sessions\n- ..."`
- In orchestrator, before building prompt: `memory_lines = self._memory.get_history_lines(10)`

### What NOT to change

- **SenseReading** — no need for wall_timestamp. MemoryStore uses `time.time()` at recording time.
- **daily_summaries** — create table for forward compat, don't populate yet.
- **Existing running gags** — session memory augments them, doesn't replace. In-session counts stay in memory, cross-session totals come from SQLite.

## Event Recording Matrix

| Event | Trigger | Stored |
|-------|---------|--------|
| App switch | `_last_seen_app` changes | App name (known list only) |
| Idle return | Idle sense reading with "returned" | "Returned after X minutes" |
| Session start | Boot | Timestamp |
| Session end | Shutdown | Timestamp |
| Comment milestone | Every 10th comment | Count |

## Files Modified

| File | Change |
|------|--------|
| `tokenpal/brain/memory.py` | **NEW** — MemoryStore class |
| `tokenpal/config/schema.py` | Add MemoryConfig |
| `tokenpal/config/loader.py` | Add "memory" to _SECTION_MAP |
| `config.default.toml` | Add [memory] section |
| `tokenpal/app.py` | Create/wire MemoryStore lifecycle |
| `tokenpal/brain/orchestrator.py` | Event recording + pass memory_lines to prompt |
| `tokenpal/brain/personality.py` | Accept memory_lines, add {memory_block} to template |

## Verification

1. **Unit test MemoryStore**: Create with `:memory:`, record observations, verify `get_history_lines()` output
2. **Permissions test**: Verify db file is 0o600 on macOS/Linux
3. **Integration**: Run TokenPal, switch apps, restart, verify cross-session counts appear in prompt
4. **Token budget**: Verify `get_history_lines(10)` stays under ~100 tokens
5. **Tail log**: `tail -f ~/.tokenpal/logs/tokenpal.log` — confirm recording events appear at debug level
