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
- `python3 setup_tokenpal.py` — one-command setup (venv, deps, Ollama, config)
- `./run.sh` or `tokenpal` — run the buddy
- `tokenpal --check` — verify Ollama, model, senses, actions
- `tokenpal --verbose` — show debug logs in terminal
- `pytest` — run tests (asyncio_mode=auto)
- `ruff check tokenpal/` — lint (line-length 100, select E/F/I/N/W/UP)
- `mypy tokenpal/ --ignore-missing-imports` — type check (strict mode)

## Senses
- `app_awareness` — macOS: Quartz window list (NOT NSWorkspace). Browser titles sanitized (stripped unless matching music player patterns)
- `hardware` — psutil, cross-platform, expressive summaries at high utilization
- `time_awareness` — cross-platform, session duration tracking
- `idle` — pynput, cross-platform, three tiers, transition-only readings
- `weather` — Open-Meteo API (free, no key), poll 30min, TTL 1hr, weight 0.0. Opt-in via `/zip` command. `[weather]` config section
- `music` — macOS: AppleScript for Music.app/Spotify. Checks `System Events` before querying (prevents auto-launch). Track names redacted from logs
- `productivity` — derives from MemoryStore: time-in-app, switches/hour, streaks. MemoryStore injected via `sense_configs`. Filters sensitive app names

## Brain
- `PersonalityEngine`: rotating few-shot examples, mood system (6 moods, custom per voice), running gags, guardrails (sensitive apps, late-night tone)
- Three prompt paths: `build_prompt()` (observations), `build_freeform_prompt()` (unprompted thoughts), `build_conversation_prompt()` (user input)
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
- `/voice [train|switch|list|off|info|finetune|finetune-setup]` — voice management
- `/server [status|switch]` — server connection
- `/zip <zipcode>` — set weather location (geocodes via Open-Meteo, writes to config.toml)

## LLM Notes
- Default model: `gemma4` via Ollama. Supports tool calling.
- `disable_reasoning: true` sends `reasoning_effort: "none"` — without this, gemma4 burns ~900 tokens thinking
- Qwen3 models use `<think>` tags → empty responses via OpenAI-compat API. Don't use.
- Response filter: strips asterisks, leaked tags, prefixes, orphan punctuation
- Tool calling: Ollama's OpenAI-compat API with `tools` parameter. `tool_choice` not supported.

## Voice Training
- `/voice train <wiki> "<character>"` — generates persona, greetings, custom mood names, structure hints via 5 parallel Ollama calls
- Custom moods: pipe-delimited prompt, `_parse_custom_moods()` regex parser
- Profiles saved to `~/.tokenpal/voices/<slug>.json`, auto-activated in config.toml
- Voice persona replaces default TokenPal identity (not appended)
- Response filter sentence cap relaxed for voices: observations 3, conversations 4

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
