# TokenPal

Cross-platform AI desktop buddy. ASCII character observes your screen via modular "senses" and generates witty commentary using a local LLM.

## Quick Start
- Run: `./run.sh` (macOS/Linux) or `.\run.ps1` (Windows). Auto-syncs deps, activates venv, launches buddy.
- Fresh machine: `bash scripts/install-macos.sh` / `install-linux.sh` / `powershell scripts/install-windows.ps1`.
- Config: edit `~/.tokenpal/config.toml` (gitignored per machine).

## Sub-Documentation

Load the relevant doc on demand rather than reading all of them.

| Doc | When to read |
|-----|--------------|
| [docs/claude/senses.md](docs/claude/senses.md) | Touching a sense (`tokenpal/senses/<name>/`), adding a new one, or debugging why a reading isn't emitting |
| [docs/claude/actions.md](docs/claude/actions.md) | Adding or modifying an LLM-callable tool (`@register_action`) |
| [docs/claude/brain.md](docs/claude/brain.md) | Editing orchestrator, personality, pacing, idle-tool rolls, utility wedges (intent/EOD/rage/git-nudge), or observation enrichment |
| [docs/claude/ui.md](docs/claude/ui.md) | Touching Qt (`tokenpal/ui/qt/`), Textual (`textual_overlay.py`), chat log, speech bubbles, or buddy environment particles |
| [docs/claude/slash-commands.md](docs/claude/slash-commands.md) | Adding a slash command or changing one's behavior (`/options`, `/voice`, `/ask`, `/research`, `/cloud`, etc.) |
| [docs/claude/llm.md](docs/claude/llm.md) | Editing `HttpBackend`, cloud backend, max_tokens scaling, prompt caching, or tool-calling wiring |
| [docs/claude/voice.md](docs/claude/voice.md) | Voice training, ASCII art generation, or remote fine-tuning pipeline |
| [docs/claude/server.md](docs/claude/server.md) | Editing `tokenpal/server/`, launch scripts, or inference-engine proxy behavior |

## Architecture
- Dual inference backend: `[llm] inference_engine = "ollama" | "llamacpp"`. Ollama is default (NVIDIA/Apple/RDNA3). llamacpp gates worker VRAM-unload (taskkill vs API), model registration, and `/model pull|browse` slash commands. See `docs/amd-dgpu-setup.md`
- Plugin discovery: `@register_sense` / `@register_backend` / `@register_overlay` / `@register_action` decorators + `pkgutil.walk_packages`
- Config: TOML (`config.default.toml` defaults, `config.toml` user overrides gitignored) → dataclass schema in `tokenpal/config/schema.py`
- Config loading: location-independent — finds defaults relative to package, searches `~/.tokenpal/config.toml` → project root → cwd
- Threading: async brain loop in daemon thread, Textual UI on main thread. Communication via `post_message()` (thread-safe)
- User input: Textual `Input` widget, routes via `brain.submit_user_input()` (asyncio.Queue + call_soon_threadsafe)
- Senses produce `SenseReading` with `.summary` (natural language, NOT bracketed tags), `.changed_from` (transition metadata), `.confidence`, per-sense `reading_ttl_s`
- Data directory: configurable via `[paths] data_dir` in config (default `~/.tokenpal`), holds logs/, memory.db, voices/
- Audio I/O: opt-in via `[audio]` (both `voice_conversation_enabled` and `speak_ambient_enabled` default off). Output (TTS) and input (wake/VAD/ASR) sides are kept structurally separate so ambient narration alone never opens a mic — `tokenpal/audio/` + `tests/test_audio/test_modularity.py` enforce this. See `plans/say-what.md`

## Key Commands
- Platform installers: `bash scripts/install-macos.sh`, `powershell scripts/install-windows.ps1`, `bash scripts/install-linux.sh` — standalone fresh-machine setup with interactive client/server/both prompt and VRAM-based model recommendation
- `python3 setup_tokenpal.py` — lightweight setup for when Python is already installed. `--client` for remote GPU, `--local` for full local
- `tokenpal` — run the buddy (first-run wizard on fresh install)
- `./run.sh` (macOS/Linux), `.\run.ps1` (Windows) — day-to-day launchers at the **repo root** (NOT `scripts/`). Activate venv, auto-sync deps via `pip install -e .[<extras>]` when `pyproject.toml` is newer than `.venv/.tokenpal-deps-synced`, then exec tokenpal with passed args. Force resync: `TOKENPAL_FORCE_SYNC=1`. `scripts/` holds one-shot installers + utilities, never runtime wrappers -- edit `run.sh`/`run.ps1` in place when touching launch behavior
- `tokenpal --check` — quick verify: inference engine, model, senses, actions
- `tokenpal --validate` — full preflight: Python version, platform deps, git, inference engine, model, config, senses, macOS permissions
- `tokenpal --verbose` — show debug logs in terminal
- `tokenpal --skip-welcome` -- bypass first-run wizard
- `tokenpal --overlay {auto|qt|textual|console|tkinter}` -- override `[ui] overlay` from config at launch (e.g. `--overlay textual` for rich TUI in the terminal without editing config.toml)
- `scripts/download-model.ps1` -- interactive GGUF picker for llamacpp path (Windows). Downloads, updates config + bat.
- `pytest` -- run tests (asyncio_mode=auto)
- `ruff check tokenpal/` — lint (line-length 100, select E/F/I/N/W/UP)
- `mypy tokenpal/ --ignore-missing-imports` — type check (strict mode)

## Privacy
- No clipboard monitoring (explicitly rejected)
- Sensitive app exclusion: banking, passwords, health, messaging — goes silent
- Browser window titles sanitized (stripped unless matching music player patterns)
- Session memory stores only app names and timestamps, never content
- During active conversations, user messages are held in memory (not saved to disk) until the session times out (~2 min of silence). Conversation buffer is cleared on sensitive app detection. User input truncated to 30 chars in log output
- Log files and memory.db at 0o600 (owner-only)
- Music track names redacted from DEBUG logs
- Network senses/commands — all opt-in, all keyless by default: `weather` (Open-Meteo), `world_awareness` (HN Algolia), `/ask` (DuckDuckGo + Wikipedia; Brave via `TOKENPAL_BRAVE_KEY` env var stubbed). All untrusted external text wrapped in delimiters + filtered via `contains_sensitive_term` before any prompt composition. `/ask` shows an explicit first-use consent warning; queries never persisted to disk
- Lat/lon rounded to 1 decimal. No ip-api.com. No clipboard monitoring
- Audio I/O is opt-in via `[audio]` toggles, both default off. Voice conversation requires `AUDIO_INPUT` + `AUDIO_OUTPUT` consent; ambient narration requires `AUDIO_OUTPUT` only and never opens a mic. No cloud STT/TTS — all audio stays local

## Platform Notes
- macOS: use `alpha` transparency on tkinter, NOT `systemTransparent`
- macOS: app awareness uses Quartz `CGWindowListCopyWindowInfo` (NOT `NSWorkspace`)
- Overlay selection: Qt default, Textual fallback (headless/no-DISPLAY). See [docs/claude/ui.md](docs/claude/ui.md)
- `config.toml` is gitignored -- each machine has its own
- Windows + RDNA 4 (gfx1201): Ollama's Vulkan backend produces wrong numerics on dense models; ROCm backend can't enumerate the card. Use `[llm] inference_engine = "llamacpp"` with lemonade-sdk's llama.cpp-rocm build. See `docs/amd-dgpu-setup.md`

## UI / Qt Conventions
- When a Qt change involves painting, translucency, or custom widget rendering, verify the approach actually paints on screen before declaring done. Stylesheet approaches can be silently blocked by `WA_TranslucentBackground` and similar flags — reach for `paintEvent` when stylesheets mysteriously don't render.

## Code Style
- Python 3.12+, strict mypy, ruff for linting
- abc.ABC for abstractions, dataclasses for data, ClassVar for registry metadata
- Sense implementations go in `tokenpal/senses/<sense_name>/<platform>_impl.py`

## Repo
- GitHub: github.com/smabe/TokenPal (private)
- 4 target machines: Mac (M-series), Dell XPS 16 (Intel iGPU), AMD laptop (RTX 4070), AMD desktop (RX 9070 XT)
- Dev setup guides in `docs/dev-setup-*.md`
- Plan files in `plans/` — active plans track current work, shipped plans in `plans/shipped/`
- Open issues: `gh issue list`
