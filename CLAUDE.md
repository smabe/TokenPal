# TokenPal

Cross-platform AI desktop buddy. ASCII character observes your screen via modular "senses" and generates sarcastic commentary using a local LLM.

## Architecture
- Plugin discovery: `@register_sense` / `@register_backend` / `@register_overlay` / `@register_action` decorators + `pkgutil.walk_packages`
- Config: TOML (`config.default.toml` defaults, `config.toml` user overrides gitignored) → dataclass schema in `tokenpal/config/schema.py`
- Config loading: location-independent — finds defaults relative to package, searches `~/.tokenpal/config.toml` → project root → cwd
- Threading: async brain loop in daemon thread, sync UI on main thread. Communication via `overlay.schedule_callback()`
- User input: main thread captures keystrokes in cbreak mode, routes via `brain.submit_user_input()` (asyncio.Queue + call_soon_threadsafe)
- Senses produce `SenseReading` with natural-language `.summary` (NOT bracketed tags — LLMs echo those back)
- Per-sense polling intervals via `ClassVar[float]` on `AbstractSense` (e.g., hardware 10s, time 30s, idle 1s)
- Interestingness scoring: per-sense weights in `context.py`, read-only scoring + explicit `acknowledge()` after comment
- Actions: `AbstractAction` with `@register_action`, tool specs sent to LLM via OpenAI-compat tools API, multi-turn execution loop (max 3 rounds)
- Data directory: configurable via `[paths] data_dir` in config (default `~/.tokenpal`), holds logs/, memory.db, voices/
- Session memory: SQLite at `{data_dir}/memory.db`, records app switches + idle returns, injects history into prompts

## Key Commands
- `python3 setup_tokenpal.py` — one-command setup (venv, deps, Ollama, config)
- `./run.sh` or `tokenpal` — run the buddy
- `tokenpal --check` — verify Ollama, model, senses, actions
- `tokenpal --verbose` — show debug logs in terminal
- `tokenpal --config PATH` — use specific config file
- `ollama serve` / `brew services start ollama` — LLM backend must be running
- `pytest` — run tests (55 tests, asyncio_mode=auto)
- `ruff check tokenpal/` — lint (line-length 100, select E/F/I/N/W/UP)
- `mypy tokenpal/ --ignore-missing-imports` — type check (strict mode)
- `tail -f ~/.tokenpal/logs/tokenpal.log` — debug log (DEBUG level, rotated at 5MB)

## Senses
- `app_awareness` (macOS: Quartz window list, NOT NSWorkspace — NSWorkspace is unreliable from background threads)
- `hardware` (psutil, cross-platform, expressive summaries at high utilization)
- `time_awareness` (cross-platform, session duration tracking)
- `idle` (pynput, cross-platform, three tiers: short/medium/long, transition-only readings)

## Brain
- `PersonalityEngine`: rotating few-shot examples (25 pool, sample 5-7), comment history deque, structure hints, mood system (6 moods), running gags (dynamic app detection), guardrails (sensitive apps, compliment ratio, late-night tone)
- Two prompt paths: `build_prompt()` for observations (strict: 1 sentence, 70 chars, [SILENT] allowed), `build_conversation_prompt()` for user input (relaxed: 2 sentences, 150 chars, always responds)
- Shared `_clean_llm_text()` with pre-compiled regex constants for response cleanup
- `ContextWindowBuilder`: per-sense weighted interestingness, acknowledge pattern prevents consumed changes
- Easter eggs bypass LLM (3:33 AM, Friday 5 PM, Zoom → "Condolences.", Calculator → "Math. Voluntarily.")
- Graceful degradation: confused quips when Ollama is unreachable, "Ollama unreachable" pushed to status bar on first failure
- Session memory: `MemoryStore` in `tokenpal/brain/memory.py`, cross-session app visit counts + history lines in prompt
- User input: `submit_user_input()` (thread-safe), `_handle_user_input()` (async, max_tokens=100)

## Actions
- `tokenpal/actions/` package with `@register_action` decorator pattern
- `AbstractAction`: `action_name`, `description`, `parameters` (JSON Schema), `execute(**kwargs) -> ActionResult`, `teardown()`
- Built-in: `timer` (countdown, 1hr cap, 5 concurrent max), `system_info` (psutil stats), `open_app` (safety-allowlisted)
- Tool specs sent to LLM via Ollama's OpenAI-compat `/v1/chat/completions` with `tools` parameter
- Multi-turn execution: Brain loops up to 3 rounds, parallel tool calls via `asyncio.gather()`
- `ActionsConfig` in schema with per-action enable flags

## Slash Commands
- `tokenpal/commands.py`: `CommandDispatcher` with `CommandResult` dataclass
- Built-in: `/help`, `/clear`, `/mood`, `/status`, `/model [name]`
- `/model` swaps the LLM model at runtime via `AbstractLLMBackend.set_model()`
- Dispatched from main thread, results shown as speech bubbles

## UI
- Console overlay: bottom-anchored layout, typing animation (30ms/char), live status bar (mood + senses + last spoke)
- Text input: cbreak mode (termios), non-blocking stdin via `select.select()`, dirty-flag coalesced redraws
- Input line rendered between bottom border and status bar: `> typed text here_`
- Buddy art: block-character chonky design with Cyrillic eyes, 4 expressions (idle/talking/thinking/surprised)
- Tkinter overlay: alternative, always-on-top window (less reliable than console)

## LLM Notes
- Default model: `gemma3:4b` via Ollama. Fast (<1s), decent at short quips. Supports tool calling.
- Qwen3 models use internal `<think>` tags → empty responses via OpenAI-compat API. Don't use.
- `max_tokens: 40` for observations, `100` for conversation responses.
- Response filter: strips asterisks, leaked tags, prefixes. Observation: 1 sentence, 15-70 chars. Conversation: 2 sentences, 5-150 chars.
- Tool calling: Ollama's OpenAI-compat API with `tools` parameter. `tool_choice` not supported (model decides). Arguments come as JSON strings.
- `AbstractLLMBackend.set_model()` for runtime model swap. `model_name` property exposed on base class.
- Prompt template in `personality.py` has mood line, structure hint, examples, session notes, memory block, context, recent comments.

## Platform Notes
- macOS: use `alpha` transparency on tkinter, NOT `systemTransparent` (causes text overlap)
- macOS: app awareness uses Quartz `CGWindowListCopyWindowInfo` (NOT `NSWorkspace.frontmostApplication()`)
- Console overlay (`overlay = "console"`) is default and more reliable than tkinter
- `config.toml` is gitignored — each machine has its own (different model, backend, senses)

## Privacy
- No clipboard monitoring (explicitly rejected — privacy liability)
- Sensitive app exclusion list: banking, password managers, health apps, messaging — goes silent
- Session memory stores only app names and timestamps, never content/URLs/keystrokes
- SQLite db at `{data_dir}/memory.db` with 0o600 permissions
- Browser content guardrails deferred — needs tab title/URL awareness

## Code Style
- Python 3.12+, strict mypy, ruff for linting
- abc.ABC for abstractions, dataclasses for data, ClassVar for registry metadata
- Sense implementations go in `tokenpal/senses/<sense_name>/<platform>_impl.py`

## Repo
- GitHub: github.com/smabe/TokenPal (private)
- 4 target machines: Mac (M-series), Dell XPS 16 (Intel NPU), AMD laptop (RTX 4070), AMD desktop (RX 9070 XT)
- Dev setup guides: `docs/dev-setup-macos.md`, `docs/dev-setup-windows-intel.md`, `docs/dev-setup-windows-amd.md`, `docs/dev-setup-windows-amd-desktop.md`
- Plan files in `plans/` — shipped plans marked `[SHIPPED]`, `npu-buddy-exploration.md` is the original vision doc

## What's Left
- Better ASCII art (user designing their own)
- Daily summaries in SQLite (end-of-day aggregation)
- MLX backend (native macOS inference, skip Ollama)
- Music detection sense
- Windows app_awareness impl (macOS-only right now)
- Windows text input (needs msvcrt instead of termios)
- Browser content guardrails (needs tab title/URL filtering)
- Security review (pre-release audit of input listeners + privacy surface)
- Voice commands via slash commands (pushed to far future)
