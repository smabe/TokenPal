# Plan: Make data directory configurable (not hardcoded to `~/.tokenpal`) [SHIPPED]

## Context

All data paths (`~/.tokenpal/voices`, `~/.tokenpal/logs`, `~/.tokenpal/memory.db`) are hardcoded as module-level constants scattered across 4 files. This makes it impossible to relocate the data directory (e.g., for portable installs, testing, or multi-instance setups). The good news: downstream functions already accept paths as parameters — only the top-level constants need to change.

## Approach

Add a single `data_dir` field to config. All subdirectories derive from it. No per-subdirectory config fields (YAGNI).

### 1. Add `data_dir` to config schema

**File**: `tokenpal/config/schema.py`

Add to `TokenPalConfig` (top-level, not nested in a section):
```python
data_dir: str = "~/.tokenpal"
```

### 2. Add `[paths]` section to `config.default.toml`

```toml
[paths]
data_dir = "~/.tokenpal"
```

Update schema to have a `PathsConfig` dataclass with `data_dir: str = "~/.tokenpal"`, add `paths: PathsConfig` to `TokenPalConfig`.

### 3. Resolve and pass `data_dir` through app bootstrap

**File**: `tokenpal/app.py`

- Replace `_DATA_DIR = Path.home() / ".tokenpal"` with resolution from config:
  ```python
  data_dir = Path(config.paths.data_dir).expanduser().resolve()
  ```
- Pass `data_dir` to `setup_logging(data_dir / "logs")`, voice loading (`data_dir / "voices"`), and `MemoryStore(data_dir / "memory.db")` — these call sites already exist, just swap the source.

### 4. Update logging to accept a path parameter

**File**: `tokenpal/util/logging.py`

- Change `setup_logging()` to accept an optional `log_dir: Path | None` parameter
- Fall back to `Path.home() / ".tokenpal" / "logs"` if `None` (keeps standalone CLI tools working without config)

### 5. Update `train_voice.py` to read from config

**File**: `tokenpal/tools/train_voice.py`

This file has two hardcoded constants:
- `_VOICES_DIR = Path.home() / ".tokenpal" / "voices"` (line 29) — used in 4 places: `_cmd_list()` (lines 164, 167), `_cmd_activate()` (line 177), `_cmd_extract()` (line 310)
- `_OLLAMA_URL = "http://localhost:11434/v1/chat/completions"` (line 30) — should respect `config.llm.api_base` if available

Changes:
- Add a `_get_voices_dir() -> Path` helper that loads config via `load_config()` and returns `Path(config.paths.data_dir).expanduser() / "voices"`, falling back to `~/.tokenpal/voices` if config loading fails
- Add a `_get_ollama_url() -> str` helper that reads `config.llm.api_base`, falling back to the current hardcoded URL
- Replace all 4 `_VOICES_DIR` references with `_get_voices_dir()` calls
- Replace `_OLLAMA_URL` usage with `_get_ollama_url()` calls (lines 30, 44)

### 6. Update config loader search path

**File**: `tokenpal/config/loader.py`

- `_USER_CONFIG_DIR` is used only for finding `config.toml` — this stays as `~/.tokenpal` since config must be found *before* `data_dir` is known. No change needed here.

## Files to modify

| File | Change |
|------|--------|
| `tokenpal/config/schema.py` | Add `PathsConfig` dataclass + field on `TokenPalConfig` |
| `config.default.toml` | Add `[paths]` section |
| `tokenpal/app.py` | Resolve `data_dir` from config, replace `_DATA_DIR` constant |
| `tokenpal/util/logging.py` | Accept optional `log_dir` param in `setup_logging()` |
| `tokenpal/tools/train_voice.py` | Resolve voices dir from config with fallback |

## Verification

1. `python -m tokenpal --check` — should work with default config (no `[paths]` section)
2. Set `data_dir = "/tmp/tokenpal-test"` in config.toml, run TokenPal — logs/memory/voices should appear under `/tmp/tokenpal-test/`
3. `python -m tokenpal.tools.train_voice --list` — should work standalone without config.toml present (fallback path)
4. Existing `~/.tokenpal` installs with no `[paths]` section continue working unchanged
