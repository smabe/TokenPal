# TokenPal

Cross-platform AI desktop buddy. ASCII character observes your screen via modular "senses" and generates witty commentary using a local LLM.

## Architecture
- Plugin discovery: `@register_sense` / `@register_backend` / `@register_overlay` / `@register_action` decorators + `pkgutil.walk_packages`
- Config: TOML (`config.default.toml` defaults, `config.toml` user overrides gitignored) → dataclass schema in `tokenpal/config/schema.py`
- Config loading: location-independent — finds defaults relative to package, searches `~/.tokenpal/config.toml` → project root → cwd
- Threading: async brain loop in daemon thread, sync UI on main thread. Communication via `overlay.schedule_callback()`
- User input: main thread captures keystrokes in cbreak mode, routes via `brain.submit_user_input()` (asyncio.Queue + call_soon_threadsafe)
- Senses produce `SenseReading` with `.summary` (natural language, NOT bracketed tags), `.changed_from` (transition metadata), `.confidence`, per-sense `reading_ttl_s`
- Data directory: configurable via `[paths] data_dir` in config (default `~/.tokenpal`), holds logs/, memory.db, voices/

## Key Commands
- `python3 setup_tokenpal.py` — one-command setup (venv, deps, Ollama, config). `--client` for remote GPU, `--local` for full local
- `tokenpal` — run the buddy (first-run wizard on fresh install)
- `tokenpal --check` — verify Ollama, model, senses; warns on enabled-but-unimplemented senses
- `tokenpal --verbose` — show debug logs in terminal
- `tokenpal --skip-welcome` — bypass first-run wizard
- `pytest` — run tests (asyncio_mode=auto)
- `ruff check tokenpal/` — lint (line-length 100, select E/F/I/N/W/UP)
- `mypy tokenpal/ --ignore-missing-imports` — type check (strict mode)

## Senses
- `app_awareness` — macOS: Quartz window list (NOT NSWorkspace). Browser titles sanitized (stripped unless matching music player patterns)
- `hardware` — psutil, cross-platform, expressive summaries at high utilization
- `time_awareness` — cross-platform, session duration tracking
- `idle` — pynput, cross-platform, three tiers, transition-only readings
- `weather` — Open-Meteo API (free, no key), poll 30min, TTL 1hr, weight 0.0. Opt-in via `/zip` command or first-run wizard. Geocoding + config write in `tokenpal/config/weather.py`
- `music` — macOS: AppleScript for Music.app/Spotify. Checks `System Events` before querying (prevents auto-launch). Track names redacted from logs
- `productivity` — derives from MemoryStore: time-in-app, switches/hour, streaks. MemoryStore injected via `sense_configs`. Filters sensitive app names

## Brain
- `PersonalityEngine`: tiered few-shot examples (anchor lines for recency priming), mood system (6 moods, custom per voice), running gags, guardrails (sensitive apps, late-night tone, cross-franchise filter)
- Three prompt paths: `build_prompt()` (observations), `build_freeform_prompt()` (unprompted thoughts), `build_conversation_prompt()` (user input, single-turn fallback)
- Multi-turn conversation: `ConversationSession` in orchestrator tracks history buffer, `build_conversation_system_message()` + `build_context_injection()` compose the messages array. Config in `[conversation]` section. Observations/freeform suppressed during active session. Session auto-expires after `timeout_s` (default 120s). History capped at `max_turns` pairs (default 10, limited by gemma4's 4-8k context — bump for larger models)
- `ContextWindowBuilder`: per-sense weighted interestingness, acknowledge pattern, composite observations (`_detect_composites()`), public API: `active_readings()`, `prev_summary()`, `ttl_for()`
- Topic roulette: `_pick_topic()` in orchestrator, no 3+ consecutive same-topic, focus hints prepended to context
- Change detection: `changed_from` field on `SenseReading`, app_awareness populates "switched from X"
- Pacing: dynamic cooldown (30-90s based on activity), max 8 comments/5min, forced 2-min silence after 3 consecutive, timing jitter
- Freeform thoughts: 15% default, 30% for rich voices (50+ example lines), 45s min gap
- Easter eggs bypass LLM (3:33 AM, Friday 5 PM, Zoom, Calculator)
- See `plans/commentary-finetune-master.md` for full commentary system design

## Slash Commands
- `/help`, `/clear`, `/mood`, `/status`
- `/model [name|list|pull|browse]` — model management
- `/voice [train|switch|list|off|info|finetune|finetune-setup|regenerate|import]` — voice management
- `/server [status|switch]` — server connection
- `/zip <zipcode>` — set weather location (geocodes via Open-Meteo, writes to config.toml)

## LLM Notes
- Default model: `gemma4` via Ollama. Supports tool calling.
- `disable_reasoning: true` sends `reasoning_effort: "none"` — without this, gemma4 burns ~900 tokens thinking
- Qwen3 models use `<think>` tags → empty responses via OpenAI-compat API. Don't use.
- Response filter: strips asterisks, emojis, leaked tags, prefixes, orphan punctuation. Cross-franchise name filter suppresses responses mentioning characters from wrong show
- Tool calling: Ollama's OpenAI-compat API with `tools` parameter. `tool_choice` not supported.

## Voice Training
- `/voice train`, `/voice regenerate` — structured persona cards with catchphrase priming and cross-franchise guardrails
- See `docs/voice-training.md` for persona format, anchor lines, banned names, and architecture

## Fine-Tuning
- Remote LoRA fine-tuning via SSH. Recommended: `google/gemma-2-2b-it` on RTX 4070 (~15 min Windows, ~7 min Linux)
- Two platform paths: native Windows (PowerShell) and Linux/WSL (tmux). ROCm works for RDNA 3; RDNA 4 blocked until ROCm 7.3+
- Pipeline: build wheel → push bundle → install → push base model → prep data → train → merge → pull → register Ollama
- See `docs/remote-training-guide.md` for setup, config, troubleshooting, and developer gotchas

## Server
- `tokenpal/server/` package: FastAPI inference proxy + training orchestration
- Byte-forwarding `/v1/*` proxy to local Ollama (streaming-ready)
- Auto-fallback: server unreachable → tries `localhost:11434/v1`
- `OLLAMA_KEEP_ALIVE=1m` in `start-server.bat` — frees VRAM when idle
- See `docs/server-setup.md` for setup guide

## Privacy
- No clipboard monitoring (explicitly rejected)
- Sensitive app exclusion: banking, passwords, health, messaging — goes silent
- Browser window titles sanitized (stripped unless matching music player patterns)
- Session memory stores only app names and timestamps, never content
- During active conversations, user messages are held in memory (not saved to disk) until the session times out (~2 min of silence). Conversation buffer is cleared on sensitive app detection. User input truncated to 30 chars in log output
- Log files and memory.db at 0o600 (owner-only)
- Music track names redacted from DEBUG logs
- Weather is the ONLY network request (Open-Meteo, opt-in). No ip-api.com. Lat/lon rounded to 1 decimal

## Platform Notes
- macOS: use `alpha` transparency on tkinter, NOT `systemTransparent`
- macOS: app awareness uses Quartz `CGWindowListCopyWindowInfo` (NOT `NSWorkspace`)
- Console overlay is default and more reliable than tkinter
- `config.toml` is gitignored — each machine has its own

## Code Style
- Python 3.12+, strict mypy, ruff for linting
- abc.ABC for abstractions, dataclasses for data, ClassVar for registry metadata
- Sense implementations go in `tokenpal/senses/<sense_name>/<platform>_impl.py`

## Repo
- GitHub: github.com/smabe/TokenPal (private)
- 4 target machines: Mac (M-series), Dell XPS 16 (Intel NPU), AMD laptop (RTX 4070), AMD desktop (RX 9070 XT)
- Dev setup guides in `docs/dev-setup-*.md`
- Plan files in `plans/` — active plans track current work, shipped plans in `plans/shipped/`
- Open issues: `gh issue list`
